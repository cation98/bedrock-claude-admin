import hashlib
import base64
import logging
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as DBSession

from app.core.config import Settings, get_settings
from app.core.database import get_db

security_scheme = HTTPBearer()
# auto=False: Bearer 토큰이 없어도 422를 발생시키지 않음 (Pod 내부 인증 fallback 허용)
security_scheme_optional = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)


def generate_app_slug(username: str, attempt: int = 0) -> str:
    """사번에서 8자리 해시 slug 생성 (URL/K8s 리소스명용). attempt로 충돌 회피.

    사번(e.g. N1102359)을 SHA-256 해시하여 앞 8자를 반환한다.
    URL에서 사번이 노출되지 않도록 비식별화하는 용도.
    attempt > 0이면 입력에 attempt를 포함하여 다른 slug을 생성한다.
    """
    data = f"{username.lower()}:{attempt}" if attempt > 0 else username.lower()
    return hashlib.sha256(data.encode()).hexdigest()[:8]


def encode_password(password: str, salt: str) -> str:
    """SSO 비밀번호 인코딩 (O-Guard 패턴 재사용).

    SHA-256(password + salt) → Base64
    """
    combined = (password + salt).encode("utf-8")
    sha256_hash = hashlib.sha256(combined).digest()
    return base64.b64encode(sha256_hash).decode("utf-8")


def create_access_token(
    data: dict,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """JWT 액세스 토큰 생성."""
    if settings is None:
        settings = get_settings()

    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_token(token: str, settings: Settings | None = None) -> dict:
    """JWT 토큰 검증 및 페이로드 반환.

    실패 시 HTTPException(401)을 발생시킨다.
    예외를 던지지 않는 버전이 필요하면 decode_token()을 사용.
    """
    if settings is None:
        settings = get_settings()

    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


def decode_token(token: str, settings: Settings | None = None) -> dict | None:
    """JWT 토큰 디코딩 (실패 시 None 반환).

    verify_token()과 동일한 로직이지만, 예외 대신 None을 반환한다.
    Auth Proxy처럼 인증 실패를 리다이렉트로 처리해야 하는 곳에서 사용.
    """
    if settings is None:
        settings = get_settings()

    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    settings: Settings = Depends(get_settings),
) -> dict:
    """현재 인증된 사용자 정보를 반환하는 FastAPI dependency."""
    return verify_token(credentials.credentials, settings)


async def get_current_user_or_pod(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme_optional),
    settings: Settings = Depends(get_settings),
    db: DBSession = Depends(get_db),
) -> dict:
    """JWT 인증 또는 Pod 내부 인증 (X-Pod-Name 헤더).

    브라우저(Hub 포털): JWT 토큰 → get_current_user 방식
    Pod 내부(Claude Code): X-Pod-Name 헤더 → 세션에서 사용자 조회

    Returns:
        dict: {"sub": username, "role": role, ...}
              Pod 인증 시 추가로 "auth_type": "pod" 포함.
    """
    # 1. JWT 인증 시도 (Bearer 토큰이 있는 경우)
    if credentials and credentials.credentials:
        try:
            payload = verify_token(credentials.credentials, settings)
            return payload
        except HTTPException:
            pass  # JWT 실패 → Pod 내부 인증으로 fallback

    # 2. Pod 내부 인증 (X-Pod-Name + X-Pod-Token 헤더 모두 필요)
    pod_name = request.headers.get("X-Pod-Name", "")
    if pod_name.startswith("claude-terminal-"):
        pod_token = request.headers.get("X-Pod-Token", "")
        if not pod_token:
            logger.warning(f"Pod auth attempted for {pod_name} without X-Pod-Token header")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="X-Pod-Token header is required for pod authentication",
            )

        from app.models.session import TerminalSession

        session = (
            db.query(TerminalSession)
            .filter(
                TerminalSession.pod_name == pod_name,
                TerminalSession.pod_status.in_(["running", "creating"]),
            )
            .first()
        )
        if not session or not session.pod_token_hash:
            logger.warning(f"Pod auth failed: no active session with token for {pod_name}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authenticated",
            )

        # 제출된 토큰을 SHA-256 해시하여 저장된 해시와 비교
        # secrets.compare_digest: timing-safe 비교로 timing attack 방지
        submitted_hash = hashlib.sha256(pod_token.encode()).hexdigest()
        if not secrets.compare_digest(submitted_hash, session.pod_token_hash):
            logger.warning(f"Pod auth failed: invalid token for {pod_name}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid pod token",
            )

        logger.debug(f"Pod internal auth: {pod_name} → user {session.username}")
        return {"sub": session.username, "role": "user", "auth_type": "pod"}

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authenticated",
    )
