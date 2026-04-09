"""Kubernetes Pod 관리 서비스.

사용자 로그인 시 개인용 Claude Code 터미널 Pod을 동적으로 생성/삭제.

K8s 개념 정리:
  - Pod: 컨테이너 실행 단위 (1 Pod = 1 사용자 터미널)
  - Namespace: Pod을 논리적으로 그룹화 (claude-sessions)
  - ServiceAccount: Pod에 AWS 권한을 부여하는 "신분증"
  - Label: Pod에 메타데이터 부착 (사용자명, 세션 타입 등)
"""

import hashlib
import json as _json
import logging
import secrets
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

    def _create_pod_token_secret(self, username: str, pod_token: str) -> None:
        """Pod 인증 토큰을 K8s Secret에 저장.

        Secret 이름: pod-token-{username}
        Pod에서 환경변수 SECURE_POD_TOKEN으로 마운트한다.

        이 Secret은 Auth Gateway가 X-Pod-Token 헤더 검증에 사용하지 않는다
        (DB에 해시를 저장). Secret은 Pod 컨테이너가 토큰을 주입받기 위한
        K8s 네이티브 방식으로만 사용한다.
        """
        secret_name = f"pod-token-{username.lower()}"
        secret_body = client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=secret_name,
                namespace=self.namespace,
                labels={"app": "claude-terminal", "user": username.lower()},
            ),
            string_data={"token": pod_token},
            type="Opaque",
        )
        try:
            self.v1.create_namespaced_secret(namespace=self.namespace, body=secret_body)
            logger.info(f"Pod token secret {secret_name} created for user {username}")
        except ApiException as e:
            if e.status == 409:
                # Secret이 이미 존재하면 교체 (Pod 재생성 시)
                self.v1.replace_namespaced_secret(
                    name=secret_name,
                    namespace=self.namespace,
                    body=secret_body,
                )
                logger.info(f"Pod token secret {secret_name} replaced for user {username}")
            else:
                logger.error(f"Failed to create pod token secret: {e}")
                raise K8sServiceError(f"Failed to create pod token secret: {e.reason}")

    def _delete_pod_token_secret(self, username: str) -> None:
        """Pod 인증 토큰 Secret 삭제 (Pod 종료 시 호출)."""
        secret_name = f"pod-token-{username.lower()}"
        try:
            self.v1.delete_namespaced_secret(name=secret_name, namespace=self.namespace)
            logger.info(f"Pod token secret {secret_name} deleted")
        except ApiException as e:
            if e.status != 404:
                logger.error(f"Failed to delete pod token secret: {e}")

    def _build_volume_mounts(
        self,
        username: str,
        shared_read_only: bool,
    ) -> list:
        """Pod volume mounts 구성.

        [보안] .efs-users 전체 마운트 제거 (2026-04-09).
        공유 데이터셋은 DB에서 조회하여 개별 sub_path로 마운트.
        """
        mounts = [
            client.V1VolumeMount(
                name="user-workspace",
                mount_path="/home/node/workspace",
                sub_path=f"users/{username.lower()}",
            ),
            client.V1VolumeMount(
                name="user-workspace",
                mount_path="/home/node/workspace/shared",
                sub_path="shared",
                read_only=shared_read_only,
            ),
        ]

        # 공유 데이터셋 개별 마운트: ~/workspace/team/{owner}/{dataset_name}/
        try:
            from app.core.database import SessionLocal
            from app.models.file_share import SharedDataset, FileShareACL

            db = SessionLocal()
            # 이 사용자에게 공유된 활성 ACL 조회
            acls = (
                db.query(FileShareACL)
                .filter(
                    FileShareACL.target_username == username.upper(),
                    FileShareACL.revoked_at.is_(None),
                )
                .all()
            )
            # 각 ACL에 대응하는 데이터셋 조회 → sub_path 마운트
            dataset_ids = [a.dataset_id for a in acls]
            if dataset_ids:
                datasets = (
                    db.query(SharedDataset)
                    .filter(SharedDataset.id.in_(dataset_ids))
                    .all()
                )
                for ds in datasets:
                    owner = ds.owner_username.lower()
                    mount_path = f"/home/node/workspace/team/{ds.owner_username}/{ds.dataset_name}"
                    sub_path = f"users/{owner}/shared-data/{ds.dataset_name}"
                    mounts.append(
                        client.V1VolumeMount(
                            name="user-workspace",
                            mount_path=mount_path,
                            sub_path=sub_path,
                            read_only=True,
                        ),
                    )
                    logger.info(
                        f"Shared mount for {username}: {mount_path} → {sub_path}"
                    )
            db.close()
        except Exception as e:
            logger.warning(f"Failed to load shared mounts for {username}: {e}")

        return mounts

    def _build_env_vars(
        self,
        username: str,
        user_display_name: str,
        security_policy: dict | None,
        proxy_secret: str | None = None,
        pod_token: str | None = None,
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
            # 유휴 감지용 내부 heartbeat — Pod가 5분마다 호출
            client.V1EnvVar(
                name="AUTH_GATEWAY_URL",
                value="http://auth-gateway.platform.svc.cluster.local",
            ),
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

        # Pod 내부 API 인증 토큰 — K8s Secret에서 환경변수로 주입
        # X-Pod-Token 헤더로 Auth Gateway에 전달하여 신원 증명
        if pod_token:
            env_vars.append(client.V1EnvVar(
                name="SECURE_POD_TOKEN",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name=f"pod-token-{username.lower()}",
                        key="token",
                    )
                ),
            ))

        # 프록시 환경변수 — Pod에서 외부 API 접근 시 Auth Gateway 프록시를 거치도록 설정
        # HTTPS_PROXY/HTTP_PROXY: curl, pip, npm 등이 자동으로 프록시를 사용
        # NO_PROXY: 클러스터 내부 통신은 프록시를 우회
        # NOTE: proxy_secret은 kubectl describe pod에서 보임. K8s Secret으로 전환 검토 (Phase 2)
        if proxy_secret:
            proxy_url = (
                f"http://{username}:{proxy_secret}"
                f"@auth-gateway.platform.svc.cluster.local:3128"
            )
            env_vars.extend([
                client.V1EnvVar(name="HTTPS_PROXY", value=proxy_url),
                client.V1EnvVar(name="HTTP_PROXY", value=proxy_url),
                client.V1EnvVar(
                    name="NO_PROXY",
                    value="localhost,127.0.0.1,10.0.0.0/16,.svc.cluster.local,.cluster.local",
                ),
            ])

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
        infra_policy: dict | None = None,
    ) -> tuple[str, str | None, str | None]:
        """사용자용 Claude Code 터미널 Pod 생성.

        Args:
            username: 사번 (e.g. N1102359)
            session_type: workshop | daily
            user_display_name: 표시 이름 (SSO에서 조회, 없으면 사번 사용)
            ttl_seconds: Pod 수명(초). 0이면 unlimited (activeDeadlineSeconds 미설정).

        Returns:
            (pod_name, proxy_secret, pod_token_hash): 생성된 Pod 이름, 프록시 인증 시크릿,
            Pod 토큰의 SHA-256 해시. Pod 재사용 시 proxy_secret과 pod_token_hash는 None.
        """
        pod_name = self._pod_name(username)

        # 프록시 인증용 랜덤 시크릿 생성
        proxy_secret = secrets.token_hex(32)

        # Pod 내부 API 인증용 토큰 생성 (secrets.token_urlsafe: URL-safe base64, 32바이트 엔트로피)
        pod_token = secrets.token_urlsafe(32)

        # 이미 실행 중인 Pod이 있는지 확인
        existing = self.get_pod_status(pod_name)
        if existing and existing.get("phase") in ("Pending", "Running"):
            logger.info(f"Pod {pod_name} already exists, reusing")
            return pod_name, None, None

        # 인프라 정책 기반 Pod 리소스 결정 (DB에서 관리, 하드코딩 제거)
        from app.models.infra_policy import INFRA_TEMPLATES
        infra = infra_policy or INFRA_TEMPLATES["standard"]

        cpu_req = infra.get("cpu_request", "1700m")
        cpu_lim = infra.get("cpu_limit", "1700m")
        mem_req = infra.get("memory_request", "2900Mi")
        mem_lim = infra.get("memory_limit", "2900Mi")
        node_selector_val = infra.get("node_selector")  # dict or None
        shared_writable = infra.get("shared_dir_writable", False)

        # target_node이 지정되면 node_selector 무시 (관리자 수동 배치)
        node_selector = node_selector_val if not target_node else None
        shared_read_only = not shared_writable

        pod_manifest = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=self.namespace,
                labels={
                    "app": "claude-terminal",
                    "user": username.lower(),
                    "session-type": session_type,
                },
                annotations={
                    # 오토스케일러가 활성 사용자 Pod를 강제 퇴거하지 못하도록 방지
                    "cluster-autoscaler.kubernetes.io/safe-to-evict": "false",
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
                        # init container는 root로 실행 (chown 필요)
                        security_context=client.V1SecurityContext(
                            run_as_user=0,
                            run_as_non_root=False,
                        ),
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
                # 특정 노드 지정 또는 infra_policy 기반 노드 배치
                node_name=target_node if target_node else None,
                node_selector=node_selector,
                # 노드 taint toleration — presenter/user 노드의 dedicated taint 허용
                tolerations=[
                    client.V1Toleration(
                        key="dedicated", operator="Equal",
                        value="user", effect="NoSchedule",
                    ),
                    client.V1Toleration(
                        key="dedicated", operator="Equal",
                        value="presenter", effect="NoSchedule",
                    ),
                ],
                # 1-node-1-pod 격리: max_pods_per_node==1 템플릿에서만 활성화
                affinity=client.V1Affinity(
                    pod_anti_affinity=client.V1PodAntiAffinity(
                        required_during_scheduling_ignored_during_execution=[
                            client.V1PodAffinityTerm(
                                label_selector=client.V1LabelSelector(
                                    match_labels={"app": "claude-terminal"},
                                ),
                                topology_key="kubernetes.io/hostname",
                            ),
                        ],
                    ),
                ) if infra.get("max_pods_per_node", 3) == 1 else None,
                containers=[
                    client.V1Container(
                        name="terminal",
                        image=self.settings.k8s_pod_image,
                        image_pull_policy="Always",
                        ports=[client.V1ContainerPort(container_port=7681, name="ttyd")],
                        env=self._build_env_vars(
                            username, user_display_name, security_policy,
                            proxy_secret=proxy_secret,
                            pod_token=pod_token,
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
                        volume_mounts=self._build_volume_mounts(
                            username, shared_read_only,
                        ),
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

        # NOTE: 공유 데이터셋은 더 이상 Pod 생성 시 동적 마운트하지 않음.
        # 모든 Pod에 .efs-users/ 고정 마운트가 포함되어 있으며,
        # Pod 내부의 share-sync.sh가 60초 주기로 API를 조회하여
        # ~/workspace/team/{owner}/{name} 심링크를 생성/삭제한다.
        # 이 방식으로 공유 추가/해제 시 Pod 재시작 없이 실시간 반영된다.

        # Pod 토큰 K8s Secret 생성 (Pod 매니페스트 적용 전에 Secret이 존재해야 함)
        # DB에는 해시만 저장하고, 평문 토큰은 K8s Secret → 환경변수 경로로만 전달
        pod_token_hash = hashlib.sha256(pod_token.encode()).hexdigest()
        self._create_pod_token_secret(username, pod_token)

        try:
            self.v1.create_namespaced_pod(namespace=self.namespace, body=pod_manifest)
            logger.info(f"Pod {pod_name} created for user {username}")
        except ApiException as e:
            if e.status == 409:  # Already exists
                logger.info(f"Pod {pod_name} already exists")
                return pod_name, None, None
            logger.error(f"Failed to create pod {pod_name}: {e}")
            raise K8sServiceError(f"Failed to create pod: {e.reason}")

        # Pod에 대한 Service + Ingress 생성 (터미널 + 파일 서버 접근)
        self._create_pod_service(pod_name, username)
        self._create_pod_ingress(pod_name, username)
        return pod_name, proxy_secret, pod_token_hash

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
                                # /files/ 경로는 별도 auth-url 보호 Ingress로 분리 (아래 참조)
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

        # 2) 허브 포탈 Ingress (auth-url 보호: 본인 + admin만)
        # /hub/{pod_name}/ → /portal (Hub 페이지)
        # /hub/{pod_name}/static/* → /static/* (Tabulator 등 정적 파일)
        hub_ingress = client.V1Ingress(
            metadata=client.V1ObjectMeta(
                name=f"{pod_name}-hub",
                namespace=self.namespace,
                annotations={
                    "nginx.ingress.kubernetes.io/rewrite-target": "/$2",
                    "nginx.ingress.kubernetes.io/auth-url": (
                        "http://auth-gateway.platform.svc.cluster.local"
                        "/api/v1/files/files-auth-check"
                    ),
                    "nginx.ingress.kubernetes.io/auth-response-headers": "X-Auth-Username",
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
                                    path=f"/hub/{pod_name}(/|$)(.*)",
                                    path_type="ImplementationSpecific",
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

        # 3) /files/ Ingress (auth-url 보호: 본인 Pod + admin만 접근)
        files_ingress = client.V1Ingress(
            metadata=client.V1ObjectMeta(
                name=f"{pod_name}-files",
                namespace=self.namespace,
                annotations={
                    "nginx.ingress.kubernetes.io/rewrite-target": "/$2",
                    "nginx.ingress.kubernetes.io/proxy-body-size": "100m",
                    "nginx.ingress.kubernetes.io/auth-url": (
                        "http://auth-gateway.platform.svc.cluster.local"
                        "/api/v1/files/files-auth-check"
                    ),
                    "nginx.ingress.kubernetes.io/auth-response-headers": "X-Auth-Username",
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
                                    path=f"/files/{pod_name}(/|$)(.*)",
                                    path_type="ImplementationSpecific",
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
            self.networking.create_namespaced_ingress(namespace=self.namespace, body=files_ingress)
            logger.info(f"Files ingress {pod_name}-files created (auth-url protected)")
        except ApiException as e:
            if e.status != 409:
                logger.error(f"Failed to create files ingress: {e}")

    def delete_pod(self, pod_name: str, username: str | None = None) -> bool:
        """Pod + Service + Ingress + Token Secret 삭제.

        Args:
            pod_name: 삭제할 Pod 이름 (e.g. claude-terminal-n1102359)
            username: Pod 소유자 사번. 제공 시 pod-token-{username} Secret도 삭제.
        """
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

        # Files Ingress 삭제
        try:
            self.networking.delete_namespaced_ingress(name=f"{pod_name}-files", namespace=self.namespace)
        except ApiException:
            pass

        # Pod 토큰 Secret 삭제 (username이 제공된 경우)
        if username:
            self._delete_pod_token_secret(username)

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
            # Extract username from pod name (claude-terminal-{username}) for Secret cleanup
            username = pod["name"].replace("claude-terminal-", "").upper()
            if self.delete_pod(pod["name"], username=username):
                deleted += 1
        return deleted
