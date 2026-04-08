"""웹앱 배포 서비스.

사용자가 개인 Pod에서 개발한 웹앱을 별도 상시 App Pod로 배포.
- K8s 리소스 생성/삭제 (Pod, Service, Ingress)
- DB 배포 레코드 관리 (deployed_apps, app_acl)
- ACL 기반 접근 제어

K8s 개념 정리:
  - claude-sessions 네임스페이스: 개인 터미널 Pod (개발용)
  - claude-apps 네임스페이스: 배포된 웹앱 Pod (상시 운영)
  - EFS subPath: 사용자별 격리된 스토리지 (같은 EFS, 다른 PVC)
  - Ingress auth-url: 요청마다 auth-gateway에 ACL 검증 위임
"""

import json as _json
import logging
from datetime import datetime, timezone

from kubernetes import client
from kubernetes.client.rest import ApiException
from kubernetes.client import NetworkingV1Api
from sqlalchemy.orm import Session as DbSession

from app.core.config import Settings
from app.models.app import DeployedApp, AppACL
from app.models.user import User

logger = logging.getLogger(__name__)

# 앱 Pod 배포 네임스페이스 (개인 터미널과 분리)
APP_NAMESPACE = "claude-apps"

# 앱 런타임 컨테이너 이미지 (ECR)
APP_RUNTIME_IMAGE = (
    "680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/app-runtime:latest"
)

# 앱 Pod용 EFS PVC (claude-apps 네임스페이스에 별도 생성 필요)
APP_EFS_PVC_NAME = "efs-apps-pvc"


class AppDeployError(Exception):
    """앱 배포 작업 실패."""
    pass


class AppDeployService:
    """웹앱 배포 엔진 — K8s 리소스 생성 + DB 레코드 관리."""

    def __init__(self, settings: Settings):
        self.settings = settings

        # K8s 클라이언트 초기화 (k8s_service.py와 동일한 패턴)
        if settings.k8s_in_cluster:
            from kubernetes import config as k8s_config
            k8s_config.load_incluster_config()
        else:
            from kubernetes import config as k8s_config
            k8s_config.load_kube_config()

        self.v1 = client.CoreV1Api()
        self.networking = NetworkingV1Api()

    # ------------------------------------------------------------------ #
    #  Pod/Service/Ingress 이름 생성 헬퍼
    # ------------------------------------------------------------------ #

    @staticmethod
    def _app_pod_name(username: str, app_name: str) -> str:
        """앱 Pod 이름 생성. K8s 네이밍 규칙(소문자, 하이픈)에 맞춤."""
        safe_user = username.lower().replace("_", "-")
        safe_app = app_name.lower().replace("_", "-")
        return f"app-{safe_user}-{safe_app}"

    @staticmethod
    def _app_url(username: str, app_name: str) -> str:
        """앱 접근 URL 경로 생성."""
        return f"/apps/{username.lower()}/{app_name.lower()}/"

    # ------------------------------------------------------------------ #
    #  환경변수 빌드
    # ------------------------------------------------------------------ #

    def _build_app_env_vars(
        self,
        username: str,
        app_name: str,
        version: str,
        security_policy: dict | None,
    ) -> list:
        """App Pod 환경변수 목록 생성.

        배포자의 security_policy에서 DB 자격증명을 상속.
        k8s_service._build_env_vars 패턴을 간소화하여 앱 실행에 필요한 것만 주입.
        """
        policy = security_policy or {}
        db_access = policy.get("db_access", {})
        # 정책이 없으면 하위 호환: 모든 DB 접근 허용
        safety_allowed = db_access.get("safety", {}).get("allowed", True) if db_access else True
        tango_allowed = db_access.get("tango", {}).get("allowed", True) if db_access else True

        env_vars = [
            # 앱 메타데이터
            client.V1EnvVar(name="APP_NAME", value=app_name),
            client.V1EnvVar(name="APP_OWNER", value=username),
            client.V1EnvVar(name="APP_VERSION", value=version),
            # Auth Gateway 내부 URL (ACL 검증, 헬스체크용)
            client.V1EnvVar(
                name="AUTH_GATEWAY_URL",
                value="http://auth-gateway.platform.svc.cluster.local:8000",
            ),
        ]

        # 배포자의 보안 정책에 따라 DB 자격증명 조건부 주입
        if safety_allowed:
            env_vars.append(client.V1EnvVar(
                name="DATABASE_URL",
                value=self.settings.workshop_database_url,
            ))

        if tango_allowed:
            env_vars.append(client.V1EnvVar(
                name="TANGO_DATABASE_URL",
                value=self.settings.tango_database_url,
            ))

        logger.info(
            f"App env for {username}/{app_name}: "
            f"safety={safety_allowed}, tango={tango_allowed}"
        )
        return env_vars

    # ------------------------------------------------------------------ #
    #  K8s 리소스 생성
    # ------------------------------------------------------------------ #

    def _create_app_pod(
        self,
        pod_name: str,
        username: str,
        app_name: str,
        version: str,
        security_policy: dict | None,
    ) -> None:
        """App Pod 생성.

        볼륨 마운트:
          - /app (readOnly): EFS deployed/{app_name}/current/ → 앱 소스 코드
          - /data (readWrite): EFS deployed/{app_name}/data/ → 업로드 파일 등 영속 데이터
        """
        # EFS subPath: users/{username}/deployed/{app_name}/
        base_sub_path = f"users/{username.lower()}/deployed/{app_name.lower()}"

        pod_manifest = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=APP_NAMESPACE,
                labels={
                    "app": "claude-webapp",
                    "owner": username.lower(),
                    "app-name": app_name.lower(),
                },
                annotations={
                    # 오토스케일러가 운영 중인 앱 Pod를 강제 퇴거하지 못하도록 방지
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
                # init container: /data 디렉토리 권한 설정 (최초 배포 시 필요)
                init_containers=[
                    client.V1Container(
                        name="init-data-dir",
                        image="busybox",
                        security_context=client.V1SecurityContext(
                            run_as_user=0,
                            run_as_non_root=False,
                        ),
                        command=[
                            "sh", "-c",
                            "mkdir -p /data && chown -R 1000:1000 /data && chmod 755 /data",
                        ],
                        volume_mounts=[
                            client.V1VolumeMount(
                                name="app-storage",
                                mount_path="/data",
                                sub_path=f"{base_sub_path}/data",
                            ),
                        ],
                    ),
                ],
                # 앱 Pod는 재시작 허용 (운영 서비스이므로)
                restart_policy="Always",
                service_account_name=self.settings.k8s_service_account,
                containers=[
                    client.V1Container(
                        name="app",
                        image=APP_RUNTIME_IMAGE,
                        ports=[client.V1ContainerPort(container_port=3000, name="http")],
                        env=self._build_app_env_vars(
                            username, app_name, version, security_policy,
                        ),
                        # Container-level 보안 (OWASP A05):
                        # 권한 상승 차단 + 모든 capabilities 제거 + 읽기전용 루트 파일시스템
                        security_context=client.V1SecurityContext(
                            allow_privilege_escalation=False,
                            capabilities=client.V1Capabilities(drop=["ALL"]),
                            read_only_root_filesystem=True,
                        ),
                        # 앱 Pod 리소스: 터미널 Pod보다 작은 사이즈
                        resources=client.V1ResourceRequirements(
                            requests={"cpu": "250m", "memory": "512Mi"},
                            limits={"cpu": "500m", "memory": "1Gi"},
                        ),
                        readiness_probe=client.V1Probe(
                            http_get=client.V1HTTPGetAction(path="/", port=3000),
                            initial_delay_seconds=5,
                            period_seconds=10,
                        ),
                        liveness_probe=client.V1Probe(
                            http_get=client.V1HTTPGetAction(path="/", port=3000),
                            initial_delay_seconds=10,
                            period_seconds=30,
                        ),
                        volume_mounts=[
                            # 앱 소스 코드 (current symlink → 특정 버전)
                            client.V1VolumeMount(
                                name="app-storage",
                                mount_path="/app",
                                sub_path=f"{base_sub_path}/current",
                                read_only=True,
                            ),
                            # 영속 데이터 디렉토리 (업로드 파일 등)
                            client.V1VolumeMount(
                                name="app-storage",
                                mount_path="/data",
                                sub_path=f"{base_sub_path}/data",
                                read_only=False,
                            ),
                            # readOnlyRootFilesystem 대응: 임시 파일 쓰기용
                            client.V1VolumeMount(name="tmp", mount_path="/tmp"),
                        ],
                    ),
                ],
                volumes=[
                    client.V1Volume(
                        name="app-storage",
                        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                            claim_name=APP_EFS_PVC_NAME,
                        ),
                    ),
                    # OWASP A05: readOnlyRootFilesystem에서 임시 파일 허용
                    client.V1Volume(
                        name="tmp",
                        empty_dir=client.V1EmptyDirVolumeSource(
                            size_limit="256Mi",
                        ),
                    ),
                ],
            ),
        )

        try:
            self.v1.create_namespaced_pod(namespace=APP_NAMESPACE, body=pod_manifest)
            logger.info(f"App Pod {pod_name} created for {username}/{app_name}")
        except ApiException as e:
            if e.status == 409:
                logger.info(f"App Pod {pod_name} already exists")
                return
            logger.error(f"Failed to create App Pod {pod_name}: {e}")
            raise AppDeployError(f"App Pod 생성 실패: {e.reason}")

    def _create_app_service(self, pod_name: str, username: str, app_name: str) -> None:
        """App Pod를 위한 K8s Service 생성 (port 3000)."""
        svc = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=APP_NAMESPACE,
                labels={
                    "app": "claude-webapp",
                    "owner": username.lower(),
                    "app-name": app_name.lower(),
                },
            ),
            spec=client.V1ServiceSpec(
                selector={
                    "app": "claude-webapp",
                    "owner": username.lower(),
                    "app-name": app_name.lower(),
                },
                ports=[
                    client.V1ServicePort(name="http", port=3000, target_port=3000),
                ],
            ),
        )
        try:
            self.v1.create_namespaced_service(namespace=APP_NAMESPACE, body=svc)
            logger.info(f"App Service {pod_name} created")
        except ApiException as e:
            if e.status != 409:
                logger.error(f"Failed to create App Service: {e}")

    def _create_app_ingress(self, pod_name: str, username: str, app_name: str) -> None:
        """App Pod를 위한 Ingress 생성 (auth-url ACL 검증 포함).

        경로: /apps/{username}/{app_name}(/|$)(.*)
        nginx auth-url: 매 요청마다 auth-gateway에 ACL 확인 위임.
        auth-response-headers: 인증된 사용자명을 앱에 전달.
        """
        ingress = client.V1Ingress(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=APP_NAMESPACE,
                annotations={
                    # ── A01: Broken Access Control ──
                    # auth-url: 매 요청 시 auth-gateway에 ACL 검증 위임
                    "nginx.ingress.kubernetes.io/auth-url": (
                        "http://auth-gateway.platform.svc.cluster.local:8000"
                        "/api/v1/apps/auth-check"
                    ),
                    "nginx.ingress.kubernetes.io/auth-response-headers": "X-Auth-Username",
                    # auth-signin: 401 반환 시 로그인 페이지로 리다이렉트
                    "nginx.ingress.kubernetes.io/auth-signin": (
                        "https://claude.skons.net/webapp-login"
                        "?return_url=$scheme://$host$request_uri"
                    ),

                    # ── A02: Cryptographic Failures ──
                    # HTTPS 강제, HSTS 헤더
                    "nginx.ingress.kubernetes.io/force-ssl-redirect": "true",
                    "nginx.ingress.kubernetes.io/ssl-redirect": "true",

                    # ── A03: Injection + A07: XSS ──
                    # 보안 응답 헤더 (CSP, XSS Protection, Content-Type 스니핑 방지)
                    "nginx.ingress.kubernetes.io/configuration-snippet": (
                        "more_set_headers \"X-Content-Type-Options: nosniff\";\n"
                        "more_set_headers \"X-Frame-Options: SAMEORIGIN\";\n"
                        "more_set_headers \"X-XSS-Protection: 1; mode=block\";\n"
                        "more_set_headers \"Referrer-Policy: strict-origin-when-cross-origin\";\n"
                        "more_set_headers \"Permissions-Policy: camera=(), microphone=(), geolocation=()\";\n"
                        "more_set_headers \"Content-Security-Policy: default-src 'self'; "
                        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                        "style-src 'self' 'unsafe-inline'; "
                        "img-src 'self' data: blob:; "
                        "font-src 'self' data:; "
                        "connect-src 'self'; "
                        "frame-ancestors 'self'\";\n"
                    ),

                    # ── A04: Insecure Design — Rate Limiting ──
                    "nginx.ingress.kubernetes.io/limit-rps": "30",
                    "nginx.ingress.kubernetes.io/limit-connections": "10",

                    # ── A05: Security Misconfiguration ──
                    # 서버 정보 헤더 숨김
                    "nginx.ingress.kubernetes.io/server-snippet": (
                        "server_tokens off;"
                    ),

                    # ── A06: Vulnerable Components — 업로드 크기 제한 ──
                    "nginx.ingress.kubernetes.io/proxy-body-size": "50m",

                    # ── A09: Security Logging ──
                    # 접근 로그 활성화 (기본 nginx 로그에 기록)
                    "nginx.ingress.kubernetes.io/enable-access-log": "true",

                    # URL 리라이트: /apps/{user}/{app}/foo → /foo
                    "nginx.ingress.kubernetes.io/rewrite-target": "/$2",
                    # 프록시 타임아웃 설정 (SSE, WebSocket 등 지원)
                    "nginx.ingress.kubernetes.io/proxy-read-timeout": "600",
                    "nginx.ingress.kubernetes.io/proxy-send-timeout": "600",
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
                                    path=f"/apps/{username.lower()}/{app_name.lower()}(/|$)(.*)",
                                    path_type="ImplementationSpecific",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name=pod_name,
                                            port=client.V1ServiceBackendPort(number=3000),
                                        ),
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )
        try:
            self.networking.create_namespaced_ingress(
                namespace=APP_NAMESPACE, body=ingress,
            )
            logger.info(f"App Ingress {pod_name} created")
        except ApiException as e:
            if e.status != 409:
                logger.error(f"Failed to create App Ingress: {e}")

    # ------------------------------------------------------------------ #
    #  K8s 리소스 삭제
    # ------------------------------------------------------------------ #

    def _delete_app_resources(self, pod_name: str) -> None:
        """App Pod + Service + Ingress 일괄 삭제."""
        # Pod 삭제
        try:
            self.v1.delete_namespaced_pod(
                name=pod_name, namespace=APP_NAMESPACE, grace_period_seconds=10,
            )
            logger.info(f"App Pod {pod_name} deleted")
        except ApiException as e:
            if e.status != 404:
                logger.error(f"Failed to delete App Pod {pod_name}: {e}")
                raise AppDeployError(f"App Pod 삭제 실패: {e.reason}")

        # Service 삭제
        try:
            self.v1.delete_namespaced_service(name=pod_name, namespace=APP_NAMESPACE)
            logger.info(f"App Service {pod_name} deleted")
        except ApiException as e:
            if e.status != 404:
                logger.error(f"Failed to delete App Service: {e}")

        # Ingress 삭제
        try:
            self.networking.delete_namespaced_ingress(name=pod_name, namespace=APP_NAMESPACE)
            logger.info(f"App Ingress {pod_name} deleted")
        except ApiException as e:
            if e.status != 404:
                logger.error(f"Failed to delete App Ingress: {e}")

    # ------------------------------------------------------------------ #
    #  공개 API: 배포 / 삭제 / 롤백 / 조회
    # ------------------------------------------------------------------ #

    def deploy_app(
        self,
        username: str,
        app_name: str,
        version: str,
        acl_usernames: list[str],
        db: DbSession,
    ) -> DeployedApp:
        """앱 배포: K8s 리소스 생성 + DB 레코드 저장.

        전제: 호출 시점에 EFS에 스냅샷이 이미 준비되어 있어야 함.
        (터미널 Pod의 /deploy 스크립트가 로컬 복사 후 이 API를 호출)

        Args:
            username: 배포자 사번
            app_name: 앱 이름
            version: 배포 버전 (git tag 또는 자동 생성 타임스탬프)
            acl_usernames: 접근 허용할 사용자 사번 목록
            db: DB 세션

        Returns:
            DeployedApp: 생성된 배포 레코드

        Raises:
            AppDeployError: 배포 권한 없음 또는 K8s 리소스 생성 실패
        """
        # 1) 배포 권한 확인: can_deploy_apps=True 필요
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise AppDeployError(f"사용자를 찾을 수 없습니다: {username}")
        if not getattr(user, "can_deploy_apps", False):
            raise AppDeployError(
                f"배포 권한이 없습니다. 관리자에게 can_deploy_apps 승인을 요청하세요."
            )

        pod_name = self._app_pod_name(username, app_name)
        app_url = self._app_url(username, app_name)

        # 2) 기존 배포가 running이면 K8s 리소스 교체 (재배포)
        existing = (
            db.query(DeployedApp)
            .filter(
                DeployedApp.owner_username == username,
                DeployedApp.app_name == app_name,
                DeployedApp.status == "running",
            )
            .first()
        )
        if existing:
            logger.info(f"기존 배포 발견 — 재배포: {pod_name}")
            self._delete_app_resources(pod_name)
            existing.status = "replaced"
            existing.updated_at = datetime.now(timezone.utc)
            db.flush()

        # 3) K8s 리소스 생성 (Pod → Service → Ingress)
        security_policy = user.security_policy
        self._create_app_pod(pod_name, username, app_name, version, security_policy)
        self._create_app_service(pod_name, username, app_name)
        self._create_app_ingress(pod_name, username, app_name)

        # 4) DB 배포 레코드 저장
        deployed_app = DeployedApp(
            owner_username=username,
            app_name=app_name,
            app_url=app_url,
            pod_name=pod_name,
            status="running",
            version=version,
        )
        db.add(deployed_app)
        db.flush()  # ID 확보를 위해 flush (commit은 호출자가 관리)

        # 5) ACL 레코드 저장 — 배포자 본인도 자동 포함
        acl_set = set(acl_usernames)
        acl_set.add(username)  # 배포자 본인은 항상 접근 가능
        for acl_username in acl_set:
            acl = AppACL(
                app_id=deployed_app.id,
                grant_type="user",
                grant_value=acl_username,
                granted_by=username,
            )
            db.add(acl)

        db.commit()
        logger.info(
            f"앱 배포 완료: {pod_name} (version={version}, "
            f"acl={len(acl_set)}명)"
        )
        return deployed_app

    def undeploy_app(
        self,
        username: str,
        app_name: str,
        db: DbSession,
    ) -> bool:
        """앱 삭제: K8s 리소스 삭제 + DB 상태 변경.

        Args:
            username: 배포자 사번 (본인 앱만 삭제 가능)
            app_name: 앱 이름
            db: DB 세션

        Returns:
            True if 삭제 성공
        """
        deployed = (
            db.query(DeployedApp)
            .filter(
                DeployedApp.owner_username == username,
                DeployedApp.app_name == app_name,
                DeployedApp.status == "running",
            )
            .first()
        )
        if not deployed:
            raise AppDeployError(f"실행 중인 앱을 찾을 수 없습니다: {app_name}")

        pod_name = self._app_pod_name(username, app_name)

        # 1) K8s 리소스 삭제
        self._delete_app_resources(pod_name)

        # 2) DB 상태 업데이트
        deployed.status = "stopped"
        deployed.updated_at = datetime.now(timezone.utc)

        # 3) ACL도 전부 revoke 처리
        acl_entries = (
            db.query(AppACL)
            .filter(AppACL.app_id == deployed.id, AppACL.revoked_at.is_(None))
            .all()
        )
        now = datetime.now(timezone.utc)
        for acl in acl_entries:
            acl.revoked_at = now

        db.commit()
        logger.info(f"앱 삭제 완료: {pod_name}")
        return True

    def redeploy_app(
        self,
        username: str,
        app_name: str,
        version: str,
        db: DbSession,
    ) -> DeployedApp:
        """앱 재배포: 기존 Pod 삭제 → 새 Pod 생성 (current symlink가 이미 갱신된 상태).

        /deploy 스크립트가 새 스냅샷을 만들고 current symlink를 갱신한 뒤 이 API를 호출.
        """
        # 기존 배포의 ACL 목록 보존
        existing = (
            db.query(DeployedApp)
            .filter(
                DeployedApp.owner_username == username,
                DeployedApp.app_name == app_name,
                DeployedApp.status == "running",
            )
            .first()
        )
        acl_usernames = []
        if existing:
            acl_usernames = [
                acl.grant_value
                for acl in db.query(AppACL)
                .filter(AppACL.app_id == existing.id, AppACL.revoked_at.is_(None))
                .all()
            ]

        # deploy_app이 기존 배포를 자동으로 교체 처리
        return self.deploy_app(
            username=username,
            app_name=app_name,
            version=version,
            acl_usernames=acl_usernames,
            db=db,
        )

    def rollback_app(
        self,
        username: str,
        app_name: str,
        version: str,
        db: DbSession,
    ) -> DeployedApp:
        """앱 롤백: current symlink를 지정 버전으로 변경 + Pod 재시작.

        전제: 호출 시점에 터미널 Pod에서 current symlink가 이미 변경되어 있어야 함.
        이 API는 Pod만 재시작하고 DB 버전을 업데이트.
        """
        deployed = (
            db.query(DeployedApp)
            .filter(
                DeployedApp.owner_username == username,
                DeployedApp.app_name == app_name,
                DeployedApp.status == "running",
            )
            .first()
        )
        if not deployed:
            raise AppDeployError(f"실행 중인 앱을 찾을 수 없습니다: {app_name}")

        pod_name = self._app_pod_name(username, app_name)

        # Pod만 삭제 → restart_policy=Always이므로... 아니다,
        # restart_policy는 Pod 레벨이라 Pod 자체를 재생성해야 함.
        # Pod 삭제 후 다시 생성
        try:
            self.v1.delete_namespaced_pod(
                name=pod_name, namespace=APP_NAMESPACE, grace_period_seconds=5,
            )
            logger.info(f"롤백을 위해 App Pod {pod_name} 삭제")
        except ApiException as e:
            if e.status != 404:
                raise AppDeployError(f"App Pod 삭제 실패: {e.reason}")

        # 사용자 보안 정책 조회 (DB 접근 자격증명 주입용)
        user = db.query(User).filter(User.username == username).first()
        security_policy = user.security_policy if user else None

        # Pod 재생성 (current symlink가 이미 롤백 버전을 가리킴)
        self._create_app_pod(pod_name, username, app_name, version, security_policy)

        # DB 버전 업데이트
        deployed.version = version
        deployed.updated_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(f"앱 롤백 완료: {pod_name} → version={version}")
        return deployed

    def get_my_apps(self, username: str, db: DbSession) -> list[dict]:
        """내가 배포한 앱 목록 조회.

        Returns:
            [{"app_name", "app_url", "pod_name", "status", "version", "created_at", "acl_count"}]
        """
        apps = (
            db.query(DeployedApp)
            .filter(DeployedApp.owner_username == username)
            .order_by(DeployedApp.created_at.desc())
            .all()
        )
        result = []
        for app in apps:
            acl_count = (
                db.query(AppACL)
                .filter(AppACL.app_id == app.id, AppACL.revoked_at.is_(None))
                .count()
            )
            result.append({
                "id": app.id,
                "app_name": app.app_name,
                "app_url": app.app_url,
                "pod_name": app.pod_name,
                "status": app.status,
                "version": app.version,
                "created_at": app.created_at.isoformat() if app.created_at else None,
                "updated_at": app.updated_at.isoformat() if app.updated_at else None,
                "acl_count": acl_count,
            })
        return result

    def get_shared_apps(self, username: str, db: DbSession) -> list[dict]:
        """나에게 공유된 앱 목록 조회 (내가 배포한 것 제외).

        Returns:
            [{"app_name", "app_url", "owner", "status", "version"}]
        """
        # 활성 ACL에서 나에게 부여된 앱 ID 조회
        acl_entries = (
            db.query(AppACL)
            .filter(
                (AppACL.grant_type == "user") & (AppACL.grant_value == username),
                AppACL.revoked_at.is_(None),
            )
            .all()
        )
        app_ids = [acl.app_id for acl in acl_entries]
        if not app_ids:
            return []

        # 해당 앱 중 running 상태이며 내가 배포하지 않은 것만 필터
        apps = (
            db.query(DeployedApp)
            .filter(
                DeployedApp.id.in_(app_ids),
                DeployedApp.status == "running",
                DeployedApp.owner_username != username,
            )
            .order_by(DeployedApp.created_at.desc())
            .all()
        )
        return [
            {
                "id": app.id,
                "app_name": app.app_name,
                "app_url": app.app_url,
                "owner": app.owner_username,
                "status": app.status,
                "version": app.version,
                "created_at": app.created_at.isoformat() if app.created_at else None,
            }
            for app in apps
        ]

    # ------------------------------------------------------------------ #
    #  ACL 관리
    # ------------------------------------------------------------------ #

    def get_app_acl(
        self, username: str, app_name: str, db: DbSession,
    ) -> list[dict]:
        """앱의 활성 ACL 목록 조회."""
        deployed = (
            db.query(DeployedApp)
            .filter(
                DeployedApp.owner_username == username,
                DeployedApp.app_name == app_name,
                DeployedApp.status == "running",
            )
            .first()
        )
        if not deployed:
            raise AppDeployError(f"실행 중인 앱을 찾을 수 없습니다: {app_name}")

        acl_entries = (
            db.query(AppACL)
            .filter(AppACL.app_id == deployed.id, AppACL.revoked_at.is_(None))
            .all()
        )
        return [
            {
                "id": acl.id,
                "grant_type": acl.grant_type,
                "grant_value": acl.grant_value,
                "granted_by": acl.granted_by,
                "granted_at": acl.granted_at.isoformat() if acl.granted_at else None,
            }
            for acl in acl_entries
        ]

    def add_acl(
        self,
        username: str,
        app_name: str,
        grant_value: str,
        db: DbSession,
        grant_type: str = "user",
    ) -> AppACL:
        """앱에 사용자 접근 권한 추가."""
        deployed = (
            db.query(DeployedApp)
            .filter(
                DeployedApp.owner_username == username,
                DeployedApp.app_name == app_name,
                DeployedApp.status == "running",
            )
            .first()
        )
        if not deployed:
            raise AppDeployError(f"실행 중인 앱을 찾을 수 없습니다: {app_name}")

        # 중복 방지: 이미 활성 ACL이 있으면 스킵
        existing = (
            db.query(AppACL)
            .filter(
                AppACL.app_id == deployed.id,
                (AppACL.grant_type == grant_type) & (AppACL.grant_value == grant_value),
                AppACL.revoked_at.is_(None),
            )
            .first()
        )
        if existing:
            logger.info(f"ACL already exists: {grant_type}:{grant_value} → {app_name}")
            return existing

        acl = AppACL(
            app_id=deployed.id,
            grant_type=grant_type,
            grant_value=grant_value,
            granted_by=username,
        )
        db.add(acl)
        db.commit()
        logger.info(f"ACL 추가: {grant_type}:{grant_value} → {app_name}")
        return acl

    def revoke_acl(
        self,
        username: str,
        app_name: str,
        target_username: str,
        db: DbSession,
    ) -> bool:
        """앱에서 사용자 접근 권한 회수."""
        # 배포자 본인은 회수 불가
        if target_username == username:
            raise AppDeployError("배포자 본인의 접근 권한은 회수할 수 없습니다.")

        deployed = (
            db.query(DeployedApp)
            .filter(
                DeployedApp.owner_username == username,
                DeployedApp.app_name == app_name,
                DeployedApp.status == "running",
            )
            .first()
        )
        if not deployed:
            raise AppDeployError(f"실행 중인 앱을 찾을 수 없습니다: {app_name}")

        acl = (
            db.query(AppACL)
            .filter(
                AppACL.app_id == deployed.id,
                (AppACL.grant_type == "user") & (AppACL.grant_value == target_username),
                AppACL.revoked_at.is_(None),
            )
            .first()
        )
        if not acl:
            raise AppDeployError(f"해당 사용자의 접근 권한이 없습니다: {target_username}")

        acl.revoked_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"ACL 회수: {target_username} ← {app_name}")
        return True

    def check_access(
        self,
        username: str,
        owner_username: str,
        app_name: str,
        db: DbSession,
    ) -> bool:
        """사용자가 특정 앱에 접근 가능한지 ACL 검증 (Ingress auth-url 용).

        배포자 본인이거나 활성 ACL이 있으면 True.
        """
        if username == owner_username:
            return True

        deployed = (
            db.query(DeployedApp)
            .filter(
                DeployedApp.owner_username == owner_username,
                DeployedApp.app_name == app_name,
                DeployedApp.status == "running",
            )
            .first()
        )
        if not deployed:
            return False

        acl = (
            db.query(AppACL)
            .filter(
                AppACL.app_id == deployed.id,
                (AppACL.grant_type == "user") & (AppACL.grant_value == username),
                AppACL.revoked_at.is_(None),
            )
            .first()
        )
        return acl is not None
