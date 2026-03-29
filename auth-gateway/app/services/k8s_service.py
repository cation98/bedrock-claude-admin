"""Kubernetes Pod 관리 서비스.

사용자 로그인 시 개인용 Claude Code 터미널 Pod을 동적으로 생성/삭제.

K8s 개념 정리:
  - Pod: 컨테이너 실행 단위 (1 Pod = 1 사용자 터미널)
  - Namespace: Pod을 논리적으로 그룹화 (claude-sessions)
  - ServiceAccount: Pod에 AWS 권한을 부여하는 "신분증"
  - Label: Pod에 메타데이터 부착 (사용자명, 세션 타입 등)
"""

import json as _json
import logging
from datetime import datetime, timezone

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.client import NetworkingV1Api

from app.core.config import Settings

logger = logging.getLogger(__name__)


class K8sServiceError(Exception):
    """K8s 작업 실패."""
    pass


class K8sService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.namespace = settings.k8s_namespace

        # K8s 클라이언트 초기화
        # - in_cluster=True: EKS Pod 내부에서 실행 시 (자동 인증)
        # - in_cluster=False: 로컬 개발 시 (~/.kube/config 사용)
        if settings.k8s_in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()

        self.v1 = client.CoreV1Api()
        self.networking = NetworkingV1Api()

    def _pod_name(self, username: str) -> str:
        """사용자별 고유 Pod 이름 생성."""
        safe_name = username.lower().replace("_", "-")
        return f"claude-terminal-{safe_name}"

    def _build_env_vars(
        self,
        username: str,
        user_display_name: str,
        security_policy: dict | None,
    ) -> list:
        """Pod 환경변수 목록 생성. security_policy에 따라 DB 자격증명을 조건부 주입.

        security_policy가 None이거나 빈 dict이면 모든 DB 접근을 허용 (기존 동작 유지).
        """
        policy = security_policy or {}
        db_access = policy.get("db_access", {})
        # 정책이 없으면 하위 호환: 모든 DB 접근 허용
        safety_allowed = db_access.get("safety", {}).get("allowed", True) if db_access else True
        tango_allowed = db_access.get("tango", {}).get("allowed", True) if db_access else True
        doculog_allowed = db_access.get("doculog", {}).get("allowed", True) if db_access else True
        security_level = policy.get("security_level", "standard")

        env_vars = [
            # 항상 주입: Bedrock, 모델, 사용자 정보
            client.V1EnvVar(name="CLAUDE_CODE_USE_BEDROCK", value="1"),
            client.V1EnvVar(name="AWS_REGION", value=self.settings.bedrock_region),
            client.V1EnvVar(
                name="ANTHROPIC_DEFAULT_SONNET_MODEL",
                value=self.settings.bedrock_sonnet_model,
            ),
            client.V1EnvVar(
                name="ANTHROPIC_DEFAULT_HAIKU_MODEL",
                value=self.settings.bedrock_haiku_model,
            ),
            client.V1EnvVar(name="GIT_USER_NAME", value=user_display_name or username),
            client.V1EnvVar(name="GIT_USER_EMAIL", value=f"{username}@skons.net"),
            client.V1EnvVar(name="USER_ID", value=username),
            client.V1EnvVar(name="USER_DISPLAY_NAME", value=user_display_name or username),
            # 보안 정책 메타데이터 (컨테이너 내부에서 참조 가능)
            client.V1EnvVar(name="SECURITY_LEVEL", value=security_level),
            client.V1EnvVar(name="SECURITY_POLICY", value=_json.dumps(policy)),
        ]

        # 조건부 DB 자격증명: 정책에서 허용된 DB만 주입
        if safety_allowed:
            env_vars.append(client.V1EnvVar(
                name="DATABASE_URL",
                value=self.settings.workshop_database_url,
            ))

        if tango_allowed:
            env_vars.append(client.V1EnvVar(
                name="TANGO_DB_PASSWORD",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="auth-gateway-secrets",
                        key="TANGO_DB_PASSWORD",
                    )
                ),
            ))
            env_vars.append(client.V1EnvVar(
                name="TANGO_DATABASE_URL",
                value=self.settings.tango_database_url,
            ))

        if doculog_allowed:
            env_vars.append(client.V1EnvVar(
                name="DOCULOG_DB_PASSWORD",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="auth-gateway-secrets",
                        key="DOCULOG_DB_PASSWORD",
                    )
                ),
            ))

        logger.info(
            f"Pod env for {username}: security_level={security_level}, "
            f"safety={safety_allowed}, tango={tango_allowed}, doculog={doculog_allowed}"
        )
        return env_vars

    def create_pod(
        self,
        username: str,
        session_type: str = "workshop",
        user_display_name: str = "",
        ttl_seconds: int = 14400,
        target_node: str | None = None,
        security_policy: dict | None = None,
    ) -> str:
        """사용자용 Claude Code 터미널 Pod 생성.

        Args:
            username: 사번 (e.g. N1102359)
            session_type: workshop | daily
            user_display_name: 표시 이름 (SSO에서 조회, 없으면 사번 사용)
            ttl_seconds: Pod 수명(초). 0이면 unlimited (activeDeadlineSeconds 미설정).

        Returns:
            pod_name: 생성된 Pod 이름
        """
        pod_name = self._pod_name(username)

        # 이미 실행 중인 Pod이 있는지 확인
        existing = self.get_pod_status(pod_name)
        if existing and existing.get("phase") in ("Pending", "Running"):
            logger.info(f"Pod {pod_name} already exists, reusing")
            return pod_name

        # 보안 정책 기반 노드 등급 결정 (DB에서 관리, 하드코딩 제거)
        node_tier = (security_policy or {}).get("node_tier", "standard")
        is_premium = node_tier == "premium"

        # Premium 사용자: presenter 전용 노드 + 고사양 리소스
        node_selector = {"role": "presenter"} if (not target_node and is_premium) else None
        cpu_req = "3" if is_premium else self.settings.k8s_pod_cpu_request
        mem_req = "8Gi" if is_premium else self.settings.k8s_pod_memory_request
        cpu_lim = "3500m" if is_premium else self.settings.k8s_pod_cpu_limit
        mem_lim = "12Gi" if is_premium else self.settings.k8s_pod_memory_limit
        # Premium 사용자는 공유 디렉토리 쓰기 가능
        shared_read_only = not is_premium

        pod_manifest = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=self.namespace,
                labels={
                    "app": "claude-terminal",
                    "user": username.lower(),
                    "session-type": session_type,
                },
            ),
            spec=client.V1PodSpec(
                # Pod-level 보안 컨텍스트: 비-root 실행 강제
                security_context=client.V1PodSecurityContext(
                    run_as_non_root=True,
                    run_as_user=1000,
                    run_as_group=1000,
                    fs_group=1000,
                ),
                # EFS 디렉토리 권한 설정 (node user = UID 1000)
                init_containers=[
                    client.V1Container(
                        name="init-workspace",
                        image="busybox",
                        command=["sh", "-c", "chown -R 1000:1000 /workspace && chmod 755 /workspace && chown -R 1000:1000 /shared && chmod 755 /shared"],
                        volume_mounts=[
                            client.V1VolumeMount(
                                name="user-workspace",
                                mount_path="/workspace",
                                sub_path=f"users/{username.lower()}",
                            ),
                            client.V1VolumeMount(
                                name="user-workspace",
                                mount_path="/shared",
                                sub_path="shared",
                            ),
                        ],
                    )
                ],
                service_account_name=self.settings.k8s_service_account,
                restart_policy="Never",
                active_deadline_seconds=ttl_seconds if ttl_seconds > 0 else None,
                # 특정 노드 지정 또는 premium 사용자 전용 노드 배치
                node_name=target_node if target_node else None,
                node_selector=node_selector,
                containers=[
                    client.V1Container(
                        name="terminal",
                        image=self.settings.k8s_pod_image,
                        ports=[client.V1ContainerPort(container_port=7681, name="ttyd")],
                        env=self._build_env_vars(
                            username, user_display_name, security_policy,
                        ),
                        # Container-level 보안: 권한 상승 차단, 불필요 capabilities 제거
                        security_context=client.V1SecurityContext(
                            allow_privilege_escalation=False,
                            capabilities=client.V1Capabilities(drop=["ALL"]),
                        ),
                        resources=client.V1ResourceRequirements(
                            requests={
                                "cpu": cpu_req,
                                "memory": mem_req,
                            },
                            limits={
                                "cpu": cpu_lim,
                                "memory": mem_lim,
                            },
                        ),
                        readiness_probe=client.V1Probe(
                            http_get=client.V1HTTPGetAction(path="/", port=7681),
                            initial_delay_seconds=5,
                            period_seconds=10,
                        ),
                        liveness_probe=client.V1Probe(
                            http_get=client.V1HTTPGetAction(path="/", port=7681),
                            initial_delay_seconds=10,
                            period_seconds=30,
                        ),
                        volume_mounts=[
                            client.V1VolumeMount(
                                name="user-workspace",
                                mount_path="/home/node/workspace",
                                sub_path=f"users/{username.lower()}",
                            ),
                            # 공유 디렉토리: premium 사용자만 쓰기 가능
                            client.V1VolumeMount(
                                name="user-workspace",
                                mount_path="/home/node/workspace/shared",
                                sub_path="shared",
                                read_only=shared_read_only,
                            ),
                        ],
                    )
                ],
                volumes=[
                    client.V1Volume(
                        name="user-workspace",
                        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                            claim_name="efs-shared-pvc",
                        ),
                    ),
                ],
            ),
        )

        try:
            self.v1.create_namespaced_pod(namespace=self.namespace, body=pod_manifest)
            logger.info(f"Pod {pod_name} created for user {username}")
        except ApiException as e:
            if e.status == 409:  # Already exists
                logger.info(f"Pod {pod_name} already exists")
                return pod_name
            logger.error(f"Failed to create pod {pod_name}: {e}")
            raise K8sServiceError(f"Failed to create pod: {e.reason}")

        # Pod에 대한 Service + Ingress 생성 (터미널 + 파일 서버 접근)
        self._create_pod_service(pod_name, username)
        self._create_pod_ingress(pod_name, username)
        return pod_name

    def _create_pod_service(self, pod_name: str, username: str):
        """Pod을 위한 K8s Service 생성."""
        svc = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=self.namespace,
                labels={"app": "claude-terminal", "user": username.lower()},
            ),
            spec=client.V1ServiceSpec(
                selector={"app": "claude-terminal", "user": username.lower()},
                ports=[
                    client.V1ServicePort(name="ttyd", port=7681, target_port=7681),
                    client.V1ServicePort(name="files", port=8080, target_port=8080),
                    # 사용자 웹앱 포트 — Auth Gateway가 /app/ 프록시로 접근
                    client.V1ServicePort(name="webapp", port=3000, target_port=3000),
                ],
            ),
        )
        try:
            self.v1.create_namespaced_service(namespace=self.namespace, body=svc)
            logger.info(f"Service {pod_name} created")
        except ApiException as e:
            if e.status != 409:
                logger.error(f"Failed to create service: {e}")

    def _create_pod_ingress(self, pod_name: str, username: str):
        """Pod을 위한 Ingress 규칙 생성 (허브 + 터미널 + 파일 서버)."""
        # 1) 터미널 + 파일 서버 Ingress (rewrite /$2)
        ingress = client.V1Ingress(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=self.namespace,
                annotations={
                    "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",
                    "nginx.ingress.kubernetes.io/proxy-send-timeout": "3600",
                    "nginx.ingress.kubernetes.io/proxy-http-version": "1.1",
                    "nginx.ingress.kubernetes.io/enable-websocket": "true",
                    "nginx.ingress.kubernetes.io/proxy-body-size": "100m",
                    "nginx.ingress.kubernetes.io/rewrite-target": "/$2",
                },
            ),
            spec=client.V1IngressSpec(
                ingress_class_name="nginx",
                rules=[
                    client.V1IngressRule(
                        host="claude.skons.net",
                        http=client.V1HTTPIngressRuleValue(
                            paths=[
                                client.V1HTTPIngressPath(
                                    path=f"/terminal/{pod_name}(/|$)(.*)",
                                    path_type="ImplementationSpecific",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name=pod_name,
                                            port=client.V1ServiceBackendPort(number=7681),
                                        )
                                    ),
                                ),
                                client.V1HTTPIngressPath(
                                    path=f"/files/{pod_name}(/|$)(.*)",
                                    path_type="ImplementationSpecific",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name=pod_name,
                                            port=client.V1ServiceBackendPort(number=8080),
                                        )
                                    ),
                                ),
                                # 사용자 웹앱 → port 3000
                                client.V1HTTPIngressPath(
                                    path=f"/app/{pod_name}(/|$)(.*)",
                                    path_type="ImplementationSpecific",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name=pod_name,
                                            port=client.V1ServiceBackendPort(number=3000),
                                        )
                                    ),
                                ),
                            ]
                        ),
                    )
                ],
            ),
        )
        try:
            self.networking.create_namespaced_ingress(namespace=self.namespace, body=ingress)
            logger.info(f"Ingress {pod_name} created")
        except ApiException as e:
            if e.status != 409:
                logger.error(f"Failed to create ingress: {e}")

        # 2) 허브 포탈 Ingress (rewrite → /portal)
        hub_ingress = client.V1Ingress(
            metadata=client.V1ObjectMeta(
                name=f"{pod_name}-hub",
                namespace=self.namespace,
                annotations={
                    "nginx.ingress.kubernetes.io/rewrite-target": "/portal",
                },
            ),
            spec=client.V1IngressSpec(
                ingress_class_name="nginx",
                rules=[
                    client.V1IngressRule(
                        host="claude.skons.net",
                        http=client.V1HTTPIngressRuleValue(
                            paths=[
                                client.V1HTTPIngressPath(
                                    path=f"/hub/{pod_name}",
                                    path_type="Prefix",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name=pod_name,
                                            port=client.V1ServiceBackendPort(number=8080),
                                        )
                                    ),
                                ),
                            ]
                        ),
                    )
                ],
            ),
        )
        try:
            self.networking.create_namespaced_ingress(namespace=self.namespace, body=hub_ingress)
            logger.info(f"Hub ingress {pod_name}-hub created")
        except ApiException as e:
            if e.status != 409:
                logger.error(f"Failed to create hub ingress: {e}")

    def delete_pod(self, pod_name: str) -> bool:
        """Pod + Service + Ingress 삭제."""
        # Pod 삭제
        try:
            self.v1.delete_namespaced_pod(name=pod_name, namespace=self.namespace, grace_period_seconds=10)
            logger.info(f"Pod {pod_name} deleted")
        except ApiException as e:
            if e.status != 404:
                logger.error(f"Failed to delete pod {pod_name}: {e}")
                raise K8sServiceError(f"Failed to delete pod: {e.reason}")

        # Service 삭제
        try:
            self.v1.delete_namespaced_service(name=pod_name, namespace=self.namespace)
        except ApiException:
            pass

        # Ingress 삭제
        try:
            self.networking.delete_namespaced_ingress(name=pod_name, namespace=self.namespace)
        except ApiException:
            pass

        # Hub Ingress 삭제
        try:
            self.networking.delete_namespaced_ingress(name=f"{pod_name}-hub", namespace=self.namespace)
        except ApiException:
            pass

        return True

    def get_pod_status(self, pod_name: str) -> dict | None:
        """Pod 상태 조회."""
        try:
            pod = self.v1.read_namespaced_pod(name=pod_name, namespace=self.namespace)
            return {
                "name": pod.metadata.name,
                "phase": pod.status.phase,  # Pending, Running, Succeeded, Failed
                "pod_ip": pod.status.pod_ip,
                "start_time": pod.status.start_time,
            }
        except ApiException as e:
            if e.status == 404:
                return None
            raise K8sServiceError(f"Failed to get pod status: {e.reason}")

    def list_pods(self, label_selector: str | None = None) -> list[dict]:
        """Pod 목록 조회."""
        selector = label_selector or "app=claude-terminal"
        try:
            pods = self.v1.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=selector,
            )
            return [
                {
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "user": pod.metadata.labels.get("user", "unknown"),
                    "session_type": pod.metadata.labels.get("session-type", "unknown"),
                    "pod_ip": pod.status.pod_ip,
                    "start_time": pod.status.start_time,
                }
                for pod in pods.items
            ]
        except ApiException as e:
            logger.error(f"Failed to list pods: {e}")
            raise K8sServiceError(f"Failed to list pods: {e.reason}")

    def delete_all_pods(self, label_selector: str | None = None) -> int:
        """모든 터미널 Pod 일괄 삭제 (관리자용)."""
        pods = self.list_pods(label_selector)
        deleted = 0
        for pod in pods:
            if self.delete_pod(pod["name"]):
                deleted += 1
        return deleted
