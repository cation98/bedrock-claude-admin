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
    # 노드당 Pod 2개 제한: m5.large(2CPU/8GB) 기준 750m×2=1500m < 1700m(가용)
    k8s_pod_cpu_request: str = "750m"
    k8s_pod_cpu_limit: str = "1000m"
    k8s_pod_memory_request: str = "2Gi"
    k8s_pod_memory_limit: str = "4Gi"

    # ----- Telegram Bot -----
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""  # @봇이름 (멘션 제거용)

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

    # ----- RDS (Pod에 주입할 DB URL) -----
    workshop_database_url: str = ""  # safety-prod ReadOnly Replica
    tango_database_url: str = ""  # TANGO 알람 DB (aiagentdb) ReadOnly

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
