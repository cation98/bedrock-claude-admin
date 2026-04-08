from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Auth Gateway 설정. 환경변수 또는 .env 파일에서 로드."""

    # ----- App -----
    app_name: str = "Bedrock Claude Auth Gateway"
    debug: bool = False

    # ----- Database (플랫폼 자체 DB) -----
    database_url: str = "postgresql://postgres:postgres@localhost:5432/bedrock_claude"

    # ----- JWT -----
    jwt_secret_key: str = "change-me-in-production-use-256-bit-secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 480  # 8시간 (실습 세션 기준)

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
    bedrock_region: str = "us-east-1"
    bedrock_sonnet_model: str = "us.anthropic.claude-sonnet-4-6"
    bedrock_haiku_model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

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
    idle_timeout_minutes: int = 60          # 이 시간 이상 유휴 상태면 Pod 해제
    idle_check_interval_seconds: int = 600  # 유휴 체크 주기 (10분)

    # ----- S3 Vault -----
    s3_vault_bucket: str = ""        # 민감 파일 격리 S3 버킷 이름
    s3_vault_kms_key_id: str = ""    # KMS 키 ID (ARN 또는 별칭)
    s3_vault_region: str = "ap-northeast-2"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
