import hashlib
import base64
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

security_scheme = HTTPBearer()


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
