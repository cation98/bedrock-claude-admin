from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Auth Gateway 설정. 환경변수 또는 .env 파일에서 로드."""

    # ----- App -----
    app_name: str = "Bedrock Claude Auth Gateway"
    debug: bool = False

    # ----- Database (플랫폼 자체 DB) -----
    database_url: str = "postgresql://postgres:postgres@localhost:5432/bedrock_claude"

    # ----- JWT -----
    # jwt_secret_key / jwt_algorithm 은 레거시 필드로만 유지.
    # 신규 토큰 발급·검증은 모두 RS256(jwt_rs256.py)으로 수행한다.
    jwt_secret_key: str = "change-me-in-production-use-256-bit-secret"
    jwt_algorithm: str = "RS256"
    jwt_access_token_expire_minutes: int = 15  # 15분 (설계 §2 JWT 라이프사이클)

    # ----- SSO (sso.skons.net) -----
    sso_auth_url: str = ""  # 인증 엔드포인트
    sso_auth_url2: str = ""  # 사용자정보 조회 엔드포인트
    sso_client_id: str = ""
    sso_client_secret: str = ""
    sso_auth_method: str = "form"
    sso_scopes: str = "NeosOAuth"
    sso_token_key: str = "token"
    pw_encoding_salt: str = ""

    # ----- Kubernetes -----
    k8s_namespace: str = "claude-sessions"
    k8s_in_cluster: bool = False  # True when running inside EKS
    k8s_pod_image: str = ""  # ECR image URL
    k8s_service_account: str = "claude-terminal-sa"
    k8s_pod_ttl_seconds: int = 14400  # 4시간
    # 1node-1pod: t3.medium allocatable=1930m/3297Mi, 시스템 ~200m/~300Mi 예약
    k8s_pod_cpu_request: str = "1700m"
    k8s_pod_cpu_limit: str = "1700m"
    k8s_pod_memory_request: str = "2900Mi"
    k8s_pod_memory_limit: str = "2900Mi"

    # ----- Telegram Bot -----
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""  # @봇이름 (멘션 제거용)

    # ----- External Host (사용자 봇 webhook URL 구성용) -----
    external_host: str = "claude.skons.net"

    # ----- Bedrock (Pod에 주입할 환경변수) -----
    bedrock_region: str = "ap-northeast-2"  # 단일 출처. us-east-1 잘못된 기본값 수정.
    bedrock_sonnet_model: str = "us.anthropic.claude-sonnet-4-6"
    bedrock_haiku_model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    # T20 활성화 후 usage-worker가 SSOT이므로 token snapshot loop는 기본 비활성.
    # emergency backfill 필요 시에만 ENV로 SNAPSHOT_LOOP_ENABLED=true 설정.
    snapshot_loop_enabled: bool = False

    # ----- SMS Gateway -----
    sms_gateway_url: str = ""
    sms_auth_string: str = ""
    sms_callback_number: str = "02-6123-2200"

    # ----- 2FA -----
    two_factor_enabled: bool = True  # SMS 2FA 활성화 (False면 SSO만으로 로그인)

    # ----- Test User Bypass (개발/테스트 환경 전용) -----
    # SECURITY: 프로덕션에서는 반드시 False(기본값) 유지.
    # True로 설정하면 TEST로 시작하는 사번에 대해 SSO+2FA 우회를 허용합니다.
    # 로컬 개발 또는 CI 환경에서만 .env에 ALLOW_TEST_USERS=true 설정.
    allow_test_users: bool = False

    # ----- RDS (Pod에 주입할 DB URL) -----
    workshop_database_url: str = ""  # safety-prod ReadOnly Replica
    tango_database_url: str = ""  # TANGO 알람 DB (aiagentdb) ReadOnly
    doculog_database_url: str = ""  # Docu-Log 문서활동 분석 DB

    # ----- 유휴 Pod 자동 정리 -----
    idle_timeout_minutes: int = 30          # 이 시간 이상 유휴 상태면 Pod 해제 (2026-04-16: 60→30)
    idle_check_interval_seconds: int = 600  # 유휴 체크 주기 (10분)

    # ----- JWT RS256 (Open WebUI 통합 허브 — Phase 0) -----
    # RSA 2048-bit private key (PEM 문자열).
    # 비어 있으면 기동 시 ephemeral 키 생성 (개발/단일 레플리카용).
    # 프로덕션 다중 레플리카 환경에서는 반드시 설정해야 한다.
    jwt_rs256_private_key: str = ""

    # access token: 15분 (설계 §2 JWT 라이프사이클)
    jwt_rs256_access_expire_minutes: int = 1440  # 24h — Pod/SSO 모두 24h 기본 (2026-04-16)
    # refresh token: 12시간 (설계 §2 JWT 라이프사이클)
    jwt_refresh_token_expire_hours: int = 12

    # ----- Redis -----
    redis_url: str = ""  # e.g. redis://localhost:6379/0 — 비어 있으면 Redis 비활성화

    # ----- OnlyOffice Document Server -----
    onlyoffice_url: str = "http://onlyoffice.claude-sessions.svc.cluster.local"
    # JWT secret은 필수. 최소 32자, placeholder("CHANGE_ME_") 금지.
    # env 미주입 또는 placeholder면 앱 시작 실패 — fail-fast.
    onlyoffice_jwt_secret: str = Field(..., min_length=32)

    @field_validator("onlyoffice_jwt_secret")
    @classmethod
    def _reject_placeholder_jwt_secret(cls, v: str) -> str:
        if v.startswith("CHANGE_ME_"):
            raise ValueError(
                "onlyoffice_jwt_secret must be replaced — placeholder detected"
            )
        return v

    # ----- S3 Vault -----
    s3_vault_bucket: str = ""        # 민감 파일 격리 S3 버킷 이름
    s3_vault_kms_key_id: str = ""    # KMS 키 ID (ARN 또는 별칭)
    s3_vault_region: str = "ap-northeast-2"

    # ----- Gitea (사용자 git gateway) -----
    gitea_enabled: bool = False       # feature flag — 프로덕션 활성화 전 False 유지
    gitea_url: str = "https://gitea.internal.skons.net"              # Pod env 주입용 (사용자 git remote)
    gitea_internal_url: str = "http://gitea-http.gitea.svc.cluster.local:3000"  # 서버간 Admin API 호출용
    gitea_admin_token: str = ""       # K8s Secret에서 주입 (GITEA_ADMIN_TOKEN)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
