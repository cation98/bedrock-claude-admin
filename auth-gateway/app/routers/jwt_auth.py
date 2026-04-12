"""JWT RS256 인증 라우터 — Phase 0 Open WebUI 통합 허브.

Endpoints (설계 §2 Auth Gateway 확장):
  GET  /auth/.well-known/jwks.json  — RS256 공개키 (JWKS)
  POST /auth/pod-token-exchange      — Pod 부팅 토큰 → access + refresh JWT
  POST /auth/refresh                 — access JWT 재발급 (jti replay 감지 + cascade revoke)
  POST /auth/logout                  — refresh 무효화

Security:
  - Pod Token 1회 교환 후 즉시 blacklist
  - refresh token rotation (매 refresh 시 jti rotate)
  - jti replay → 사용자 전체 세션 revoke + 401
  - 쿠키: bedrock_ prefix, HttpOnly, Secure, SameSite=Lax, Domain=.skons.net

설계 참고:
  ~/.gstack/projects/cation98-bedrock-ai-agent/cation98-main-design-20260412-133106.md §2
"""

import hashlib
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.jwt_rs256 import (
    _verify_jwt_signature_only,
    blacklist_jti,
    blacklist_pod_token,
    create_access_token,
    create_refresh_token,
    get_jwks_dict,
    is_jti_blacklisted,
    is_pod_token_blacklisted,
    is_user_revoked,
    revoke_all_refresh_for_user,
    verify_jwt,
)
from app.models.session import TerminalSession
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["jwt-auth"])
logger = logging.getLogger(__name__)

# ─── 쿠키 설정 상수 ──────────────────────────────────────────────────────────
# 설계 §2: "모든 사내 AI 플랫폼 쿠키 이름 강제 prefix: bedrock_"
# sso.skons.net과의 이름 충돌 방지.
COOKIE_DOMAIN = ".skons.net"
ACCESS_COOKIE_NAME = "bedrock_jwt"
REFRESH_COOKIE_NAME = "bedrock_refresh"
ACCESS_TTL_SECONDS = 15 * 60         # 15분 (설계: access TTL 15분)
REFRESH_TTL_SECONDS = 12 * 60 * 60   # 12시간 (설계: refresh TTL 12시간)


# ─── Request / Response 스키마 ───────────────────────────────────────────────

class PodTokenExchangeRequest(BaseModel):
    """Pod 부팅 시 1회용 토큰 → JWT 교환 요청."""

    pod_token: str
    pod_name: str


class TokenResponse(BaseModel):
    """JWT 교환/발급 응답."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TTL_SECONDS


class RefreshRequest(BaseModel):
    """refresh 토큰으로 access 재발급 요청 (쿠키 또는 body)."""

    refresh_token: str | None = None


# ─── JWKS endpoint ───────────────────────────────────────────────────────────

@router.get("/.well-known/jwks.json")
async def jwks_endpoint():
    """RS256 공개키 JWKS 반환.

    Open WebUI, Bedrock AG 등 내부 서비스가 JWT 서명 검증에 사용.
    Cache-Control 1시간 — 빈번한 호출로 인한 부하 방지.
    """
    return JSONResponse(
        content=get_jwks_dict(),
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ─── Pod Token Exchange ───────────────────────────────────────────────────────

@router.post("/pod-token-exchange", response_model=TokenResponse)
async def pod_token_exchange(
    req: PodTokenExchangeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Pod 부팅 시 1회용 Pod Token → access + refresh JWT 교환.

    흐름:
    1. pod_token 수신 → SHA-256 해시
    2. Pod Token 블랙리스트 확인 (이미 사용된 토큰 재사용 차단)
    3. terminal_sessions에서 pod_name + pod_token_hash 매칭 세션 조회
    4. access + refresh JWT 발급
    5. Pod Token을 블랙리스트에 추가 (이후 재사용 불가)

    Security:
        Pod Token은 K8s Secret에 저장된 1회용 bootstrap credential.
        교환 성공 후 즉시 blacklist 등록 → replay 공격 차단.
        두 번째 교환 시도 시 401 반환.
    """
    # 1. Pod Token SHA-256 해시
    submitted_hash = hashlib.sha256(req.pod_token.encode()).hexdigest()

    # 2. 블랙리스트 확인 — 이미 사용된 토큰이면 즉시 거부
    if is_pod_token_blacklisted(submitted_hash):
        logger.warning(
            "Pod token replay attempt detected: pod=%s hash_prefix=%s...",
            req.pod_name,
            submitted_hash[:8],
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Pod token has already been used. Re-authentication required.",
        )

    # 3. 활성 세션 조회 (pod_name 기반)
    session = (
        db.query(TerminalSession)
        .filter(
            TerminalSession.pod_name == req.pod_name,
            TerminalSession.pod_status.in_(["running", "creating"]),
        )
        .first()
    )

    if not session or not session.pod_token_hash:
        logger.warning(
            "Pod token exchange: no active session found for pod=%s", req.pod_name
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active session found for this pod.",
        )

    # timing-safe 비교 — timing attack 방지
    if not secrets.compare_digest(submitted_hash, session.pod_token_hash):
        logger.warning(
            "Pod token exchange: hash mismatch for pod=%s", req.pod_name
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid pod token.",
        )

    # 4. 사용자 조회
    user = db.query(User).filter(User.username == session.username).first()
    if not user or not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not found or not approved.",
        )

    # 5. JWT 발급 — sub=users.id(str), emp_no=username, email 기본값
    sub = str(user.id)
    emp_no = user.username
    # SSO에서 실제 이메일 미제공 시 사번 기반 기본값
    email = f"{user.username.lower()}@skons.net"

    access_token = create_access_token(sub, emp_no, email, user.role, settings)
    refresh_token, _ = create_refresh_token(sub, emp_no, email, user.role, settings)

    # 6. Pod Token 블랙리스트 (1회 교환 완료 — 이후 재사용 차단, TTL 1시간)
    blacklist_pod_token(submitted_hash, ttl_seconds=3600)

    logger.info("Pod token exchanged successfully: pod=%s user=%s", req.pod_name, emp_no)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


# ─── Refresh ─────────────────────────────────────────────────────────────────

@router.post("/refresh")
async def refresh_token_endpoint(
    request: Request,
    response: Response,
    body: RefreshRequest | None = None,
    settings: Settings = Depends(get_settings),
):
    """Refresh token → 새 access token 발급.

    refresh token 수신: bedrock_refresh 쿠키 우선, body fallback.

    Refresh token rotation 전략:
      - 사용된 refresh jti를 즉시 blacklist (재사용 차단)
      - 동일 refresh jti 재사용 감지 → jti replay 공격으로 판단
      - replay 감지 시 해당 사용자 전체 refresh revoke (cascade) + 401

    이 엔드포인트는 /auth/pod-token-exchange 또는 SSO 로그인에서 발급한
    refresh token으로 호출된다. 브라우저의 경우 Hub landing이 4분마다 호출.
    """
    # refresh_token 추출: 쿠키 우선, body fallback
    raw_refresh = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_refresh and body:
        raw_refresh = body.refresh_token

    if not raw_refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required.",
        )

    # refresh token 기본 검증 (서명 + 만료 + type, jti blacklist 미포함)
    # jti blacklist 체크는 아래에서 명시적으로 수행 (cascade revoke 로직 포함)
    try:
        payload = _verify_jwt_signature_only(raw_refresh, expected_type="refresh")
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    sub = payload.get("sub", "")
    emp_no = payload.get("emp_no", "")
    email = payload.get("email", "")
    role = payload.get("role", "user")
    jti = payload.get("jti", "")

    # 사용자 레벨 전체 revoke 확인 (cascade revoke 후 접근 차단)
    if is_user_revoked(sub):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked. Please log in again.",
        )

    # jti replay 감지: 이미 블랙리스트에 있으면 replay 공격
    # (정상 rotate: 사용된 jti는 blacklist → 재사용 시 여기서 감지)
    if jti and is_jti_blacklisted(jti):
        # jti replay 감지 → 사용자 전체 refresh revoke (cascade)
        logger.critical(
            "JWT jti REPLAY DETECTED — revoking all sessions: sub=%s jti=%s",
            sub,
            jti,
        )
        revoke_all_refresh_for_user(sub, ttl_seconds=REFRESH_TTL_SECONDS)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Security alert: token replay detected. "
                "All sessions have been revoked. Please log in again."
            ),
        )

    # 사용된 refresh jti blacklist (rotation — 이후 재사용 차단)
    if jti:
        blacklist_jti(jti, ttl_seconds=REFRESH_TTL_SECONDS)

    # 새 access token 발급
    new_access_token = create_access_token(sub, emp_no, email, role, settings)

    # 쿠키 업데이트 (브라우저 클라이언트용)
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=new_access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        domain=COOKIE_DOMAIN,
        max_age=ACCESS_TTL_SECONDS,
    )

    logger.debug("Access token refreshed for sub=%s", sub)

    return {
        "access_token": new_access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TTL_SECONDS,
    }


# ─── Logout ──────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    body: RefreshRequest | None = None,
):
    """Refresh token 무효화 + 쿠키 삭제.

    refresh_token의 jti를 blacklist에 추가하여 재사용 차단.
    토큰이 없거나 이미 만료된 경우에도 200 반환 (멱등성).
    """
    raw_refresh = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_refresh and body:
        raw_refresh = body.refresh_token

    if raw_refresh:
        try:
            payload = verify_jwt(raw_refresh, expected_type="refresh")
            jti = payload.get("jti", "")
            if jti:
                blacklist_jti(jti, ttl_seconds=REFRESH_TTL_SECONDS)
                logger.info("Logout: refresh token revoked jti=%s", jti)
        except JWTError:
            # 이미 만료/무효인 토큰이어도 쿠키는 삭제
            pass

    # 쿠키 삭제
    response.delete_cookie(key=ACCESS_COOKIE_NAME, domain=COOKIE_DOMAIN, path="/")
    response.delete_cookie(key=REFRESH_COOKIE_NAME, domain=COOKIE_DOMAIN, path="/")

    return {"message": "Logged out successfully"}
