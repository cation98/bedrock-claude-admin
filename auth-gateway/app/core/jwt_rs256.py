"""RS256 JWT 관리자 — Phase 0 Open WebUI 통합 허브.

주요 기능:
  - RSA 2048-bit 키페어 생성 또는 PEM 환경변수 로드 (thread-safe)
  - access token (15분) / refresh token (12시간) RS256 서명
  - jti(JWT ID) 기반 replay 방지 블랙리스트
  - Pod Token 1회 교환 블랙리스트
  - JWKS 엔드포인트용 공개키 JWK 변환
  - Redis 우선, 불가 시 in-memory 블랙리스트 fallback

설계 참고:
  ~/.gstack/projects/cation98-bedrock-ai-agent/cation98-main-design-20260412-133106.md §2
"""

import base64
import hashlib
import logging
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwt

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 인메모리 블랙리스트 (Redis 없을 때 fallback)
# ═══════════════════════════════════════════════════════════════════════════════

_blacklist_lock = threading.Lock()
_blacklist: dict[str, float] = {}  # key → 만료 unix timestamp


def _blacklist_add(key: str, ttl_seconds: int) -> None:
    """인메모리 블랙리스트에 키 추가."""
    with _blacklist_lock:
        _blacklist[key] = time.time() + ttl_seconds
        # 10% 확률로 만료 항목 GC
        if secrets.randbelow(10) == 0:
            _gc_blacklist_unlocked()


def _gc_blacklist_unlocked() -> None:
    """만료된 블랙리스트 항목 제거 (반드시 _blacklist_lock 보유 상태에서 호출)."""
    now = time.time()
    expired = [k for k, exp in _blacklist.items() if exp <= now]
    for k in expired:
        del _blacklist[k]


def _blacklist_check(key: str) -> bool:
    """인메모리 블랙리스트 항목 존재 여부 확인. 만료 시 자동 제거 후 False 반환."""
    with _blacklist_lock:
        exp = _blacklist.get(key)
        if exp is None:
            return False
        if time.time() > exp:
            del _blacklist[key]
            return False
        return True


def _redis_blacklist_add(key: str, ttl_seconds: int) -> None:
    """Redis 우선, 실패 시 인메모리로 fallback하여 블랙리스트 항목 추가."""
    from app.core.redis_client import get_redis

    r = get_redis()
    if r:
        try:
            r.set(f"blacklist:{key}", "1", ex=ttl_seconds)
            return
        except Exception as e:
            logger.warning("Redis blacklist add failed, using in-memory fallback: %s", e)
    _blacklist_add(key, ttl_seconds)


def _redis_blacklist_check(key: str) -> bool:
    """Redis 우선, 실패 시 인메모리로 fallback하여 블랙리스트 항목 조회."""
    from app.core.redis_client import get_redis

    r = get_redis()
    if r:
        try:
            return bool(r.exists(f"blacklist:{key}"))
        except Exception as e:
            logger.warning("Redis blacklist check failed, using in-memory fallback: %s", e)
    return _blacklist_check(key)


def reset_blacklist_for_testing() -> None:
    """테스트 격리용 블랙리스트 초기화. 프로덕션에서는 호출 금지."""
    with _blacklist_lock:
        _blacklist.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# RSA 키 관리
# ═══════════════════════════════════════════════════════════════════════════════

_key_lock = threading.Lock()
_private_key: Optional[rsa.RSAPrivateKey] = None
_key_id: str = ""  # kid (JWKS key identifier)


def _generate_key() -> rsa.RSAPrivateKey:
    """RSA 2048-bit 키페어 생성."""
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )


def _load_key_from_pem(pem: str | bytes) -> rsa.RSAPrivateKey:
    """PEM 문자열 또는 bytes에서 RSA private key 로드."""
    return serialization.load_pem_private_key(
        pem.encode() if isinstance(pem, str) else pem,
        password=None,
    )


def _compute_kid(pem_bytes: bytes) -> str:
    """공개키 fingerprint 기반 결정론적 kid.

    2 replica가 같은 K8s Secret PEM을 로드하면 동일 kid 반환.
    SHA256(n || e)[:16] — JWKS consumer의 kid 불일치 혼란 방지.

    Phase 1a security hardening iter#8 권고 반영.

    EC/Ed25519/DH 등 RSA 외 key type은 명시적 TypeError로 reject —
    silent AttributeError 방지로 운영 debug 가능.
    """
    private = serialization.load_pem_private_key(pem_bytes, password=None)
    if not isinstance(private, rsa.RSAPrivateKey):
        raise TypeError(f"Expected RSA private key, got {type(private).__name__}")
    public = private.public_key()
    numbers = public.public_numbers()
    n_bytes = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    e_bytes = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    return hashlib.sha256(n_bytes + e_bytes).hexdigest()[:16]


def get_private_key() -> rsa.RSAPrivateKey:
    """RSA private key 반환 (lazy init, thread-safe double-checked locking).

    jwt_rs256_private_key 환경변수가 설정되어 있으면 로드.
    없으면 ephemeral 키 생성 — 다중 레플리카 환경 비권장.
    """
    global _private_key, _key_id
    if _private_key is not None:
        return _private_key

    with _key_lock:
        if _private_key is not None:
            return _private_key

        settings = get_settings()
        if settings.jwt_rs256_private_key:
            pem_bytes = (
                settings.jwt_rs256_private_key.encode()
                if isinstance(settings.jwt_rs256_private_key, str)
                else settings.jwt_rs256_private_key
            )
            _private_key = _load_key_from_pem(pem_bytes)
            logger.info("RS256 private key loaded from jwt_rs256_private_key env var.")
        else:
            _private_key = _generate_key()
            pem_bytes = _private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            logger.warning(
                "jwt_rs256_private_key not set — ephemeral RSA key generated. "
                "NOT suitable for multi-replica production deployment."
            )
        # kid = SHA256(n||e)[:16] — replica 간 결정론적 일치 보장 (Phase 1a iter#8)
        _key_id = _compute_kid(pem_bytes)
        return _private_key


def get_public_key() -> rsa.RSAPublicKey:
    """RSA public key 반환."""
    return get_private_key().public_key()


def _private_key_pem_bytes() -> bytes:
    """jose 라이브러리용 PEM bytes 반환."""
    return get_private_key().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_key_pem_bytes() -> bytes:
    """jose 라이브러리용 공개키 PEM bytes 반환."""
    return get_public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _int_to_base64url(n: int) -> str:
    """RSA 파라미터(n, e)를 Base64URL 문자열로 인코딩."""
    length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()


def get_jwks_dict() -> dict:
    """JWKS (JSON Web Key Set) 딕셔너리 반환.

    Open WebUI, Bedrock AG 등 내부 서비스가 JWT 서명 검증에 사용.
    """
    # lazy init — 키 로드 보장
    get_private_key()

    pub = get_public_key()
    nums = pub.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": _key_id,
                "n": _int_to_base64url(nums.n),
                "e": _int_to_base64url(nums.e),
            }
        ]
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 토큰 생성
# ═══════════════════════════════════════════════════════════════════════════════

def create_access_token(
    sub: str,
    emp_no: str,
    email: str,
    role: str,
    settings: Optional[Settings] = None,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[dict] = None,
) -> str:
    """RS256 access JWT 생성.

    Claims:
        sub     — SSO 사번 (username, e.g. N1102359) — 전체 consumer 합의
        emp_no  — SSO 사번 (sub와 동일, 명시성 목적으로 병기)
        email   — 사용자 이메일
        role    — user | admin
        jti     — UUID4, replay 방지용
        type    — "access"
        kid     — 서명 키 ID (JWKS kid와 일치)
        exp     — 만료 timestamp
        iat     — 발급 timestamp

    Args:
        expires_delta: 지정 시 이 값으로 TTL 오버라이드. None이면 settings 사용.
            issue #27 Pod 세션용 8h TTL 주입 경로.
        extra_claims: 페이로드에 병합할 추가 클레임. 예: {"session_type": "pod"}.
            세션 종류 구분 등 선택적 메타데이터용.
    """
    if settings is None:
        settings = get_settings()

    get_private_key()  # kid 초기화 보장

    if expires_delta is not None:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_rs256_access_expire_minutes
        )
    payload = {
        "sub": sub,
        "emp_no": emp_no,
        "email": email,
        "role": role,
        "jti": str(uuid.uuid4()),
        "type": "access",
        "kid": _key_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _private_key_pem_bytes(), algorithm="RS256")


def create_refresh_token(
    sub: str,
    emp_no: str,
    email: str,
    role: str,
    settings: Optional[Settings] = None,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[dict] = None,
) -> tuple[str, str]:
    """RS256 refresh JWT 생성.

    Args:
        sub: JWT subject (사번/username).
        emp_no: 사번.
        email: 이메일.
        role: 권한 역할.
        settings: 설정 객체. None이면 get_settings() 사용.
        expires_delta: 커스텀 만료 시간. None이면 settings.jwt_refresh_token_expire_hours 적용.
        extra_claims: 페이로드에 추가할 임의 클레임 (e.g. {"session_type": "pod"}).

    Returns:
        (token_str, jti) — jti는 호출자가 블랙리스트 관리에 활용.
    """
    if settings is None:
        settings = get_settings()

    get_private_key()  # kid 초기화 보장

    jti = str(uuid.uuid4())
    if expires_delta is not None:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            hours=settings.jwt_refresh_token_expire_hours
        )
    payload = {
        "sub": sub,
        "emp_no": emp_no,
        "email": email,
        "role": role,
        "jti": jti,
        "type": "refresh",
        "kid": _key_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _private_key_pem_bytes(), algorithm="RS256"), jti


# ═══════════════════════════════════════════════════════════════════════════════
# 토큰 검증
# ═══════════════════════════════════════════════════════════════════════════════

def _verify_jwt_signature_only(token: str, expected_type: str) -> dict:
    """RS256 JWT 서명 + 만료 + type 검증 (jti blacklist 미포함 — 내부 전용).

    refresh endpoint처럼 jti replay를 직접 처리해야 하는 곳에서 사용.
    외부 호출 시에는 verify_jwt() 사용 권장.
    """
    try:
        payload = jwt.decode(
            token,
            _public_key_pem_bytes(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except JWTError as e:
        raise JWTError(f"Token verification failed: {e}") from e

    if payload.get("type") != expected_type:
        raise JWTError(
            f"Token type mismatch: expected={expected_type}, "
            f"got={payload.get('type')}"
        )

    return payload


def verify_jwt(token: str, expected_type: str = "access") -> dict:
    """RS256 JWT 서명 + 만료 + type + jti blacklist 검증.

    일반 API 보호에 사용. refresh endpoint처럼 replay를 명시적으로
    감지해야 하는 경우는 라우터에서 직접 처리한다.

    Args:
        token: JWT 문자열
        expected_type: "access" 또는 "refresh"

    Returns:
        검증된 payload dict

    Raises:
        JWTError: 서명 무효, 만료, type 불일치, jti blacklist 포함
    """
    payload = _verify_jwt_signature_only(token, expected_type)

    # jti blacklist 확인 (로그아웃 후 재사용 차단)
    jti = payload.get("jti", "")
    if jti and _redis_blacklist_check(f"jti:{jti}"):
        raise JWTError("Token has been revoked (jti blacklisted)")

    return payload


# ═══════════════════════════════════════════════════════════════════════════════
# 블랙리스트 공개 API
# ═══════════════════════════════════════════════════════════════════════════════

def blacklist_jti(jti: str, ttl_seconds: int = 900) -> None:
    """jti를 블랙리스트에 추가.

    Args:
        jti: JWT ID
        ttl_seconds: 블랙리스트 유효 시간 (기본 15분, refresh는 12h 전달)
    """
    _redis_blacklist_add(f"jti:{jti}", ttl_seconds)


def is_jti_blacklisted(jti: str) -> bool:
    """jti가 블랙리스트에 있는지 확인."""
    return _redis_blacklist_check(f"jti:{jti}")


def blacklist_pod_token(pod_token_hash: str, ttl_seconds: int = 3600) -> None:
    """Pod Token hash를 블랙리스트에 추가 (1회 교환 후 재사용 차단).

    Args:
        pod_token_hash: SHA-256(raw_pod_token)
        ttl_seconds: 기본 1시간
    """
    _redis_blacklist_add(f"pod_token:{pod_token_hash}", ttl_seconds)


def is_pod_token_blacklisted(pod_token_hash: str) -> bool:
    """Pod Token이 이미 사용되었는지 확인."""
    return _redis_blacklist_check(f"pod_token:{pod_token_hash}")


def revoke_all_refresh_for_user(sub: str, ttl_seconds: int = 43200) -> None:
    """사용자 레벨 refresh 전체 revoke.

    jti replay 감지 시 호출. 해당 sub의 모든 refresh token을 무효화.
    ttl_seconds: refresh token TTL과 동일하게 설정 (기본 12h = 43200s).
    """
    _redis_blacklist_add(f"user_revoked:{sub}", ttl_seconds)
    logger.warning("All refresh tokens revoked for user sub=%s (replay or forced logout)", sub)


def is_user_revoked(sub: str) -> bool:
    """해당 사용자의 전체 refresh가 revoke 상태인지 확인."""
    return _redis_blacklist_check(f"user_revoked:{sub}")
