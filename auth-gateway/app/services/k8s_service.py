"""Kubernetes Pod 관리 서비스.

사용자 로그인 시 개인용 Claude Code 터미널 Pod을 동적으로 생성/삭제.

K8s 개념 정리:
  - Pod: 컨테이너 실행 단위 (1 Pod = 1 사용자 터미널)
  - Namespace: Pod을 논리적으로 그룹화 (claude-sessions)
  - ServiceAccount: Pod에 AWS 권한을 부여하는 "신분증"
  - Label: Pod에 메타데이터 부착 (사용자명, 세션 타입 등)
"""

import asyncio
import hashlib
import json as _json
import logging
import os
import re
import secrets
import tempfile
import uuid
from datetime import datetime, timezone

import io
import tarfile

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.client import NetworkingV1Api
from kubernetes.stream import stream

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

    def _gitea_env_for(self, sso_id: str, email: str) -> list:
        """Gitea 사용자 계정 확보 후 Pod 주입용 env 목록 반환.

        3회 재시도 후 모두 실패하면 GiteaProvisioningError를 올려 Pod 생성 중단.
        token_name에 UUID 접미사를 붙여 동일 사용자의 중복 토큰 이름 충돌 방지.
        """
        from app.services.gitea_client import GiteaClient, GiteaProvisioningError

        gitea = GiteaClient(
            base_url=self.settings.gitea_url,
            admin_token=self.settings.gitea_admin_token,
        )
        token_name = f"claude-pod-{sso_id.lower()}-{uuid.uuid4().hex[:8]}"
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                user_info = gitea.ensure_user(sso_id, email)
                token = gitea.issue_user_token(user_info.login, token_name)
                return [
                    client.V1EnvVar(name="GITEA_URL", value=self.settings.gitea_url),
                    client.V1EnvVar(name="GITEA_USER", value=user_info.login),
                    client.V1EnvVar(name="GITEA_TOKEN", value=token),
                ]
            except GiteaProvisioningError as exc:
                last_exc = exc
                logger.warning(
                    f"Gitea provisioning attempt {attempt}/3 failed for {sso_id}: {exc}"
                )
        raise GiteaProvisioningError(
            f"Gitea provisioning failed after 3 attempts for {sso_id}: {last_exc}"
        )

    def _build_env_vars(
        self,
        username: str,
        user_display_name: str,
        security_policy: dict | None,
        proxy_secret: str | None = None,
        pod_token: str | None = None,
        gitea_env: list | None = None,
    ) -> list:
        """Pod 환경변수 목록 생성. security_policy에 따라 DB 자격증명을 조건부 주입.

        security_policy가 None이거나 빈 dict이면 모든 DB 접근을 허용 (기존 동작 유지).
        로컬 환경(:local 이미지)에서는 외부 DB secret 참조를 건너뜀.
        """
        is_local = ":local" in self.settings.k8s_pod_image
        policy = security_policy or {}
        db_access = policy.get("db_access", {})
        # 정책이 없으면 하위 호환: 모든 DB 접근 허용
        safety_allowed = db_access.get("safety", {}).get("allowed", True) if db_access else True
        tango_allowed = (db_access.get("tango", {}).get("allowed", True) if db_access else True) and not is_local
        doculog_allowed = (db_access.get("doculog", {}).get("allowed", True) if db_access else True) and not is_local
        security_level = policy.get("security_level", "standard")

        env_vars = [
            # 항상 주입: Bedrock, 모델, 사용자 정보
            client.V1EnvVar(name="CLAUDE_CODE_USE_BEDROCK", value="1"),
            # Claude Code 자동 업데이트 차단 — 이미지 고정 버전 운용, update 루프로 인한 TUI 행업 방지
            client.V1EnvVar(name="DISABLE_AUTOUPDATER", value="1"),
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
        # 로컬에서는 pod-token Secret이 없으므로 직접 값을 주입
        if pod_token and is_local:
            env_vars.append(client.V1EnvVar(name="SECURE_POD_TOKEN", value=pod_token))
        elif pod_token:
            env_vars.append(client.V1EnvVar(
                name="SECURE_POD_TOKEN",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name=f"pod-token-{username.lower()}",
                        key="token",
                    )
                ),
            ))

        # T20: Bedrock AG HTTP proxy 활성화
        # pod_token이 있고 로컬이 아닌 경우 ANTHROPIC_BASE_URL을 Auth Gateway /v1으로 고정.
        # entrypoint.sh가 이 변수를 감지하면:
        #   1. pod-token-exchange → JWT 획득
        #   2. ANTHROPIC_AUTH_TOKEN = JWT (Claude Code가 Bearer 접두사 자동 부착)
        #   3. CLAUDE_CODE_USE_BEDROCK unset (HTTP proxy 모드로 전환)
        # 결과: Claude CLI → Auth Gateway → Bedrock (AWS SDK 직접 호출 차단)
        # 주: ANTHROPIC_API_KEY 대신 ANTHROPIC_AUTH_TOKEN 사용 — 전자는 Claude Code가
        # "Detected a custom API key" 승인 프롬프트를 띄워 UX를 저해함.
        if pod_token and not is_local:
            # Claude Code 2.x는 BASE_URL 뒤에 "/v1/messages"를 자체 부착.
            # trailing /v1 을 포함하면 최종 URL이 /v1/v1/messages 가 되어 404.
            # 기존에는 entrypoint.sh에서 ${VAR%/v1}로 trim했으나 GitHub #26에서 근본 수정.
            env_vars.append(client.V1EnvVar(
                name="ANTHROPIC_BASE_URL",
                value="http://auth-gateway.platform.svc.cluster.local",
            ))

        # 프록시 환경변수 — Pod에서 외부 API 접근 시 Auth Gateway 프록시를 거치도록 설정
        # HTTPS_PROXY/HTTP_PROXY: curl, pip, npm 등이 자동으로 프록시를 사용
        # NO_PROXY: 클러스터 내부 통신은 프록시를 우회
        # NOTE: proxy_secret은 kubectl describe pod에서 보임. K8s Secret으로 전환 검토 (Phase 2)
        if proxy_secret and not is_local:
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

        if gitea_env:
            env_vars.extend(gitea_env)

        logger.info(
            f"Pod env for {username}: security_level={security_level}, "
            f"safety={safety_allowed}, tango={tango_allowed}, doculog={doculog_allowed}, "
            f"gitea={'injected' if gitea_env else 'disabled'}"
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

        # 로컬 개발 환경 감지: 이미지 태그에 ":local"이 포함되면 Docker Desktop
        # EFS PVC → hostPath, nodeSelector/tolerations/anti-affinity 제거, imagePullPolicy=Never
        is_local = ":local" in self.settings.k8s_pod_image

        # Gitea 사용자 계정 + 토큰 프로비저닝 (feature flag + 상용 환경 한정)
        # 실패 시 GiteaProvisioningError를 올려 Pod 생성 중단 (hard fail — 설계 §5.2)
        gitea_env = None
        if self.settings.gitea_enabled and not is_local:
            gitea_env = self._gitea_env_for(username, f"{username}@skons.net")

        if is_local:
            # 로컬: hostPath 볼륨, init container 불필요, nodeSelector/tolerations 없음
            volumes = [
                client.V1Volume(
                    name="user-workspace",
                    host_path=client.V1HostPathVolumeSource(
                        path=f"/tmp/bedrock-local-data/{username.lower()}",
                        type="DirectoryOrCreate",
                    ),
                ),
            ]
            init_containers = None
            pod_node_selector = None
            pod_tolerations = None
            pod_affinity = None
            image_pull_policy = "Never"
            volume_mounts = [
                client.V1VolumeMount(
                    name="user-workspace",
                    mount_path="/home/node/workspace",
                ),
            ]
            # 로컬 리소스 제한 완화
            cpu_req = "500m"
            cpu_lim = "1000m"
            mem_req = "512Mi"
            mem_lim = "1Gi"
        else:
            # 상용: EFS PVC, init container, nodeSelector, tolerations
            volumes = [
                client.V1Volume(
                    name="user-workspace",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name="efs-shared-pvc",
                    ),
                ),
            ]
            init_containers = [
                client.V1Container(
                    name="init-workspace",
                    image="busybox",
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
            ]
            pod_node_selector = node_selector
            pod_tolerations = [
                client.V1Toleration(
                    key="dedicated", operator="Equal",
                    value="user", effect="NoSchedule",
                ),
                client.V1Toleration(
                    key="dedicated", operator="Equal",
                    value="presenter", effect="NoSchedule",
                ),
            ]
            pod_affinity = client.V1Affinity(
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
            ) if infra.get("max_pods_per_node", 3) == 1 else None
            image_pull_policy = "Always"
            volume_mounts = self._build_volume_mounts(username, shared_read_only)

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
                    "cluster-autoscaler.kubernetes.io/safe-to-evict": "false",
                },
            ),
            spec=client.V1PodSpec(
                security_context=client.V1PodSecurityContext(
                    run_as_non_root=True,
                    run_as_user=1000,
                    run_as_group=1000,
                    fs_group=1000,
                ) if not is_local else None,
                init_containers=init_containers,
                service_account_name=self.settings.k8s_service_account,
                restart_policy="Never",
                active_deadline_seconds=ttl_seconds if ttl_seconds > 0 else None,
                node_name=target_node if target_node else None,
                node_selector=pod_node_selector,
                tolerations=pod_tolerations,
                affinity=pod_affinity,
                containers=[
                    client.V1Container(
                        name="terminal",
                        image=self.settings.k8s_pod_image,
                        image_pull_policy=image_pull_policy,
                        ports=[client.V1ContainerPort(container_port=7681, name="ttyd")],
                        env=self._build_env_vars(
                            username, user_display_name, security_policy,
                            proxy_secret=proxy_secret,
                            pod_token=pod_token,
                            gitea_env=gitea_env,
                        ),
                        security_context=client.V1SecurityContext(
                            allow_privilege_escalation=False,
                            capabilities=client.V1Capabilities(drop=["ALL"]),
                        ),
                        resources=client.V1ResourceRequirements(
                            requests={"cpu": cpu_req, "memory": mem_req},
                            limits={"cpu": cpu_lim, "memory": mem_lim},
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
                        volume_mounts=volume_mounts,
                    )
                ],
                volumes=volumes,
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
                raise K8sServiceError(f"Failed to create ingress {pod_name}: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error creating ingress {pod_name}: {e}")
            raise K8sServiceError(f"Failed to create ingress {pod_name}: {e}")

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
                raise K8sServiceError(f"Failed to create hub ingress {pod_name}: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error creating hub ingress {pod_name}: {e}")
            raise K8sServiceError(f"Failed to create hub ingress {pod_name}: {e}")

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
                    "nginx.ingress.kubernetes.io/auth-signin": (
                        "https://claude.skons.net/api/v1/files/files-unauthorized"
                    ),
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
                raise K8sServiceError(f"Failed to create files ingress {pod_name}: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error creating files ingress {pod_name}: {e}")
            raise K8sServiceError(f"Failed to create files ingress {pod_name}: {e}")

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

    # -----------------------------------------------------------------
    # 파일 쓰기 (OnlyOffice 편집 저장용)
    # -----------------------------------------------------------------

    _KUBECTL_TIMEOUT_SECONDS = 120
    _CONTAINER_NAME = "terminal"
    # 컨테이너 파일 쓰기 허용 base prefix — 이 아래로만 허용.
    # /etc, /root, /proc 등으로의 경로 트래버설 차단.
    _CONTAINER_BASE_DIR = "/home/node/workspace"

    @staticmethod
    def _validate_container_path(path: str) -> str:
        """Pod 내부 절대 경로 검증.

        방어 층위:
        - 절대 경로만
        - 제어문자 차단
        - normpath로 .. 해석 후 base prefix(/home/node/workspace) 아래인지 commonpath로 확인
          (substring 체크는 /home/node/workspace-evil 같은 우회를 막지 못하므로 commonpath 사용)
        """
        if not path or not path.startswith("/"):
            raise K8sServiceError("container_path must be absolute")
        if re.search(r"[\x00-\x1f]", path):
            raise K8sServiceError("container_path contains control characters")

        normalized = os.path.normpath(path)
        # normpath 이후에도 절대 경로 유지 확인
        if not normalized.startswith("/"):
            raise K8sServiceError("container_path must resolve to absolute path")

        base = K8sService._CONTAINER_BASE_DIR
        try:
            common = os.path.commonpath([normalized, base])
        except ValueError:
            raise K8sServiceError("container_path is not within allowed base")
        if common != base:
            raise K8sServiceError(
                f"container_path must be within {base} (got {normalized!r})"
            )
        return normalized

    async def write_local_file_to_pod(
        self,
        username: str,
        container_path: str,
        local_path: str,
    ) -> None:
        """로컬 파일을 사용자 Pod 내부로 복사.

        구현 (P2-BUG3): `kubernetes.stream.stream` + tar pipe 로 exec API 직접 호출.
        auth-gateway 이미지에 `kubectl` 바이너리가 없으므로 subprocess 경로는 동작하지 않는다.
        프로젝트 내 다른 exec 호출(idle_cleanup_service, prompt_audit_service 등)과
        동일 패턴으로 통일한다.

        호출자는 디스크에 이미 쓰기 완료된 `local_path` 를 넘겨 메모리 버퍼링을 피한다.
        """
        pod_name = self._pod_name(username)
        safe_path = self._validate_container_path(container_path)
        parent = os.path.dirname(safe_path)

        if parent and parent != "/":
            await asyncio.to_thread(
                self._exec_sync,
                pod_name,
                ["mkdir", "-p", parent],
                step="mkdir",
            )

        await asyncio.to_thread(
            self._copy_local_to_pod_sync,
            pod_name,
            local_path,
            safe_path,
        )

        try:
            size = os.path.getsize(local_path)
        except OSError:
            size = -1
        logger.info(f"Wrote {size} bytes to {pod_name}:{safe_path}")

    async def write_file_to_pod(
        self,
        username: str,
        container_path: str,
        content: bytes,
    ) -> None:
        """bytes 를 Pod 에 쓰기 — 내부적으로 tempfile 에 dump 후 write_local_file_to_pod.

        대용량 파일은 write_local_file_to_pod 를 직접 호출해 메모리 버퍼링을 피할 것.
        """
        fd, tmp_path = tempfile.mkstemp(prefix="onlyoffice-save-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            await self.write_local_file_to_pod(username, container_path, tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _exec_sync(self, pod_name: str, command: list[str], *, step: str) -> None:
        """짧은 exec 명령 동기 실행. stdout/stderr 는 preload 로 수집."""
        try:
            resp = stream(
                self.v1.connect_get_namespaced_pod_exec,
                pod_name,
                self.namespace,
                container=self._CONTAINER_NAME,
                command=command,
                stdin=False,
                stderr=True,
                stdout=True,
                tty=False,
                _preload_content=True,
            )
        except ApiException as e:
            logger.error(f"exec {step} API error: {e}")
            raise K8sServiceError(f"exec {step} failed: {e.reason}")
        except Exception as e:
            logger.error(f"exec {step} failed: {e}")
            raise K8sServiceError(f"exec {step} failed: {e}")

        # preload 모드에서 stream() 은 stdout 문자열을 반환. 에러 탐지를 위해
        # 비어있지 않으면 로깅. mkdir -p 는 성공 시 출력 없음.
        if resp and isinstance(resp, str):
            logger.debug(f"exec {step} output: {resp[:200]!r}")

    def _copy_local_to_pod_sync(
        self, pod_name: str, local_path: str, dest_path: str
    ) -> None:
        """`tar xmf -` pipe 로 단일 파일을 Pod 에 복사.

        kubectl cp 는 내부적으로 `tar cf - <src> | kubectl exec -- tar xmf -` 패턴.
        여기서도 동일하게 파일 하나를 in-memory tar 로 감싸 stdin 으로 주입한다.
        64KB 단위 chunk 로 write_stdin 하여 큰 파일도 메모리 증가 상수로 전송.
        """
        # tar 아카이브를 메모리에 빌드. 한 개 파일이므로 파일 크기 + 헤더(512B) 만.
        # 50MB 편집 파일도 버퍼 피크는 ~50MB (kubectl cp 와 동일 수준).
        # 스트리밍 tar 로 더 줄일 수 있지만 구현 복잡도 vs 이득 trade-off 로 보류.
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            # arcname 은 루트 상대 경로 — Pod 쪽에서 `-C /` 로 풀면 절대 위치에 배치됨.
            tar.add(local_path, arcname=dest_path.lstrip("/"))
        tar_buf.seek(0)

        try:
            resp = stream(
                self.v1.connect_get_namespaced_pod_exec,
                pod_name,
                self.namespace,
                container=self._CONTAINER_NAME,
                command=["tar", "xmf", "-", "-C", "/"],
                stdin=True,
                stderr=True,
                stdout=True,
                tty=False,
                _preload_content=False,
            )
        except ApiException as e:
            logger.error(f"exec cp API error: {e}")
            raise K8sServiceError(f"exec cp failed: {e.reason}")

        try:
            stderr_chunks: list[str] = []
            while True:
                chunk = tar_buf.read(65536)
                if not chunk:
                    break
                resp.write_stdin(chunk)
                # 주기적으로 stderr 비우기 — 버퍼 가득 차 블록 방지
                if resp.peek_stderr():
                    stderr_chunks.append(resp.read_stderr())

            # 남은 stderr 수집 + flush 대기
            resp.update(timeout=self._KUBECTL_TIMEOUT_SECONDS)
            if resp.peek_stderr():
                stderr_chunks.append(resp.read_stderr())

            stderr_text = "".join(stderr_chunks).strip()
            if stderr_text:
                # tar 는 경고도 stderr 로 내보낼 수 있음. 에러 키워드만 실패 처리.
                lowered = stderr_text.lower()
                if "error" in lowered or "cannot" in lowered or "no such" in lowered:
                    raise K8sServiceError(f"tar copy failed: {stderr_text}")
                logger.warning(f"tar copy stderr (non-fatal): {stderr_text}")
        finally:
            try:
                resp.close()
            except Exception:
                pass

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
