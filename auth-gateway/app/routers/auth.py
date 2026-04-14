"""인증 API 라우터.

Endpoints:
  POST /api/v1/auth/login       — SSO 로그인 → 2FA 코드 발송 (or 2FA 비활성 → JWT)
  POST /api/v1/auth/verify-2fa  — 2FA 코드 검증 → JWT 발급
  POST /api/v1/auth/logout      — 로그아웃
  GET  /api/v1/auth/me          — 현재 사용자 정보
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Union

import httpx
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.jwt_rs256 import create_refresh_token as create_rs256_refresh_token
from app.core.security import create_access_token, get_current_user
from app.routers.jwt_auth import write_access_cookies
from app.models.audit_log import AuditAction
from app.models.two_factor_code import TwoFactorCode
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LoginStep1Response,
    UserInfo,
    Verify2faRequest,
)
from app.services.audit_service import log_audit
from app.services.sso_service import SSOService, SSOAuthError
from app.services.two_factor_service import (
    AccountLockedError,
    CodeExpiredError,
    CodeInvalidError,
    MaxAttemptsError,
    TwoFactorError,
    check_lockout,
    generate_code,
    verify_code,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 헬퍼 함수
# ---------------------------------------------------------------------------

def _fetch_oguard_profile(username: str, settings: Settings) -> dict | None:
    """O-Guard safety DB에서 사용자 프로필 조회 (region, team, job, first_name)."""
    workshop_url = settings.workshop_database_url
    if not workshop_url:
        return None
    try:
        conn = psycopg2.connect(workshop_url)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT p.region_name, p.team_name, p.job_name, u.first_name
            FROM auth_user u
            JOIN accounts_userprofile p ON u.id = p.user_id
            WHERE u.username = %s
        """, (username,))
        row = cur.fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception as e:
        logger.warning(f"Failed to fetch O-Guard profile for {username}: {e}")
    return None


def _fetch_oguard_phone(username: str, settings: Settings) -> str | None:
    """O-Guard safety DB에서 사용자 전화번호 조회."""
    workshop_url = settings.workshop_database_url
    if not workshop_url:
        return None
    try:
        conn = psycopg2.connect(workshop_url)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT p.phone_number
            FROM auth_user u
            JOIN accounts_userprofile p ON u.id = p.user_id
            WHERE u.username = %s
        """, (username,))
        row = cur.fetchone()
        conn.close()
        if row and row.get("phone_number"):
            return row["phone_number"]
    except Exception as e:
        logger.warning(f"Failed to fetch O-Guard phone for {username}: {e}")
    return None


def _mask_phone(phone: str) -> str:
    """전화번호 마스킹 (e.g. 010-****-1234)."""
    cleaned = phone.replace("-", "").replace(" ", "")
    if len(cleaned) >= 8:
        return cleaned[:3] + "-****-" + cleaned[-4:]
    if len(cleaned) >= 4:
        return "***-****-" + cleaned[-4:]
    return "****"


async def _send_2fa_sms(phone: str, code: str, settings: Settings) -> None:
    """SMS 게이트웨이를 통해 2FA 인증 코드 발송.

    sms.py의 SMS API 패턴을 재사용하되, 인증 불필요(시스템 발송).
    """
    sms_url = settings.sms_gateway_url
    sms_auth = settings.sms_auth_string
    sender_number = settings.sms_callback_number

    if not sms_url:
        logger.error("SMS gateway URL not configured — cannot send 2FA code")
        raise HTTPException(
            status_code=503,
            detail="SMS 서비스가 설정되지 않았습니다. 관리자에게 문의하세요.",
        )

    # 전화번호 정규화 (010-XXXX-XXXX)
    cleaned = phone.replace("-", "").replace(" ", "")
    if len(cleaned) == 11:
        formatted = f"{cleaned[:3]}-{cleaned[3:7]}-{cleaned[7:]}"
    elif len(cleaned) == 10:
        formatted = f"{cleaned[:3]}-{cleaned[3:6]}-{cleaned[6:]}"
    else:
        formatted = cleaned

    import base64 as b64lib
    message = f"[Claude Code] 인증코드: {code} (5분 이내 입력)"
    pw_base64 = b64lib.b64encode(sms_auth.encode()).decode()
    payload = {
        "TranType": "4",
        "TranPhone": formatted,
        "TranCallBack": sender_number,
        "TranMsg": message,
        "SysPw": pw_base64,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(sms_url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        result = data.get("d", {}).get("Result", {})
        if result.get("ResultCode") != "1":
            logger.error(
                f"SMS gateway returned error for 2FA: {result.get('ResultMsg')}"
            )
            raise HTTPException(
                status_code=502,
                detail="SMS 발송에 실패했습니다. 다시 시도해주세요.",
            )

        logger.info(f"2FA SMS sent to {_mask_phone(phone)}")

    except httpx.HTTPError as e:
        logger.error(f"Failed to send 2FA SMS: {e}")
        raise HTTPException(
            status_code=502,
            detail="SMS 발송에 실패했습니다. 다시 시도해주세요.",
        )


def _upsert_user(
    sso_user: dict,
    profile: dict | None,
    db: Session,
) -> tuple[User, bool]:
    """사용자 DB 등록/업데이트 후 (user, is_new) 반환."""
    user = db.query(User).filter(User.username == sso_user["username"]).first()
    is_new = False
    if not user:
        is_new = True
        user = User(
            username=sso_user["username"],
            name=profile.get("first_name") if profile else sso_user.get("name"),
            phone_number=sso_user.get("phone_number"),
            region_name=profile.get("region_name") if profile else None,
            team_name=profile.get("team_name") if profile else None,
            job_name=profile.get("job_name") if profile else None,
        )
        db.add(user)
    else:
        if profile:
            user.name = profile.get("first_name") or user.name
            user.region_name = profile.get("region_name") or user.region_name
            user.team_name = profile.get("team_name") or user.team_name
            user.job_name = profile.get("job_name") or user.job_name
        user.phone_number = sso_user.get("phone_number") or user.phone_number

    user.last_login_at = datetime.now(timezone.utc)

    # 관리자는 자동 승인
    if user.role == "admin" and not user.is_approved:
        user.is_approved = True
        user.approved_at = datetime.now(timezone.utc)

    # Auto-generate app_slug if missing (approved users only)
    if user.is_approved and not user.app_slug:
        from app.core.security import generate_app_slug
        user.app_slug = generate_app_slug(user.username)

    db.commit()
    db.refresh(user)
    return user, is_new


def _check_approval(user: User) -> None:
    """미승인 사용자이면 403 raise."""
    if not user.is_approved:
        logger.info(f"Unapproved user login attempt: {user.username}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "approval_pending",
                "message": "관리자 승인이 필요합니다. 승인 후 다시 로그인해주세요.",
            },
        )


def _issue_jwt(user: User, settings: Settings, request: Request | None = None) -> LoginResponse:
    """RS256 JWT 토큰 발급 + LoginResponse 반환.

    SEC-MED-6: admin 판별은 DB의 user.role claim 기반 — Origin/Referer 헤더 미사용.
    admin dashboard 접근 제어는 admin dashboard 자체(JWT role 확인)에서 담당한다.

    페이로드 구조(기존 경로 호환):
      sub      = user.username  (사번, 기존 엔드포인트 호환)
      user_id  = user.id
      role     = user.role
      type     = "access"
    RS256 서명 — security.create_access_token이 jwt_rs256 private key로 서명.
    """
    token_data = {
        "sub": user.username,
        "user_id": user.id,
        "role": user.role,
        "type": "access",
    }
    # admin은 refresh 인터셉터 정착 전까지 자동 로그아웃 빈도 완화 목적 8시간 부여.
    # 일반 사용자는 기존 15분 유지(Pod 플로우는 refresh 동작).
    expires_delta = timedelta(hours=8) if user.role == "admin" else None
    access_token = create_access_token(token_data, settings, expires_delta=expires_delta)
    # admin dashboard는 쿠키 미사용(Bearer localStorage)이므로 body 로 refresh 전달.
    # jwt_rs256.create_refresh_token 는 pod 플로우와 동일한 RS256 서명 키를 사용한다.
    email = f"{user.username.lower()}@skons.net"
    refresh_token, _jti = create_rs256_refresh_token(
        sub=user.username,
        emp_no=user.username,
        email=email,
        role=user.role,
        settings=settings,
    )
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        username=user.username,
        name=user.name,
        role=user.role,
    )


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------

@router.post("/login", response_model=Union[LoginStep1Response, LoginResponse])
async def login(
    request: LoginRequest,
    http_request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """SSO 로그인 (2-step).

    Step 1: SSO 인증 → 2FA 코드 SMS 발송 → code_id 반환
    - 2FA 비활성 시 JWT 즉시 발급

    Step 2: POST /verify-2fa 에서 코드 검증 후 JWT 발급
    """
    # ── 테스트 계정 SSO + 2FA 전체 우회 ──
    # SECURITY: allow_test_users=True (기본값 False)일 때만 활성화됩니다.
    # 프로덕션 환경에서는 ALLOW_TEST_USERS 환경변수를 설정하지 마십시오.
    if settings.allow_test_users and request.username.upper().startswith("TEST") and request.password == "test2026":
        user = db.query(User).filter(User.username == request.username.upper()).first()
        if user and user.is_approved:
            log_audit(db, user.username, AuditAction.LOGIN_BYPASS, detail="test account SSO+2FA skip")
            db.commit()
            logger.info(f"Test account login bypass: {user.username}")
            jwt_result = _issue_jwt(user, settings, http_request)
            write_access_cookies(response, jwt_result.access_token)
            return jwt_result

    # ── Admin 계정 SSO 매핑 ──
    # ADMIN001 → N1102359 SSO 인증, JWT는 ADMIN001로 발급
    ADMIN_SSO_MAP = {"ADMIN001": "N1102359"}
    login_username = request.username.upper()
    sso_username = ADMIN_SSO_MAP.get(login_username, request.username)

    sso_service = SSOService(settings)
    try:
        sso_user = await sso_service.authenticate(
            sso_username, request.password
        )
    except SSOAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"SSO authentication failed: {e.message}",
        )

    # Admin 매핑인 경우 SSO 결과를 admin username으로 덮어쓰기
    if login_username in ADMIN_SSO_MAP:
        sso_user["username"] = login_username
        logger.info(f"Admin SSO mapping: {login_username} → {sso_username}")

    # ── O-Guard 프로필 조회 + DB 등록/업데이트 ──
    profile = _fetch_oguard_profile(sso_user["username"], settings)
    user, is_new = _upsert_user(sso_user, profile, db)

    # ── 승인 여부 확인 ──
    _check_approval(user)

    # ── 감사 로그: SSO 인증 성공 ──
    log_audit(db, user.username, AuditAction.LOGIN_SSO)

    # ── 2FA 비활성 → JWT 즉시 발급 ──
    if not settings.two_factor_enabled:
        log_audit(db, user.username, AuditAction.LOGIN_BYPASS)
        db.commit()
        logger.info(
            f"Direct JWT issued (2fa_enabled={settings.two_factor_enabled}): "
            f"{user.username}"
        )
        jwt_result = _issue_jwt(user, settings, http_request)
        write_access_cookies(response, jwt_result.access_token)
        return jwt_result

    # ── 테스트 계정 2FA 우회 ──
    # SECURITY: allow_test_users=True (기본값 False)일 때만 활성화됩니다.
    # 프로덕션 환경에서는 ALLOW_TEST_USERS 환경변수를 설정하지 마십시오.
    if settings.allow_test_users and user.username.startswith("TEST"):
        log_audit(db, user.username, AuditAction.LOGIN_BYPASS, detail="test account 2FA skip")
        db.commit()
        logger.info(f"Test account 2FA bypass: {user.username}")
        jwt_result = _issue_jwt(user, settings, http_request)
        write_access_cookies(response, jwt_result.access_token)
        return jwt_result

    # ── 2FA 흐름 ──

    # 계정 잠금 확인
    try:
        check_lockout(user.username, db)
    except AccountLockedError as e:
        log_audit(
            db, user.username, AuditAction.LOGIN_LOCKED,
            detail=f"remaining_seconds={e.remaining_seconds}",
        )
        db.commit()
        raise HTTPException(
            status_code=423,
            detail=str(e),
            headers={"Retry-After": str(e.remaining_seconds)},
        )

    # 전화번호 확보 (User DB → SSO 응답 → O-Guard DB → phone_lookup)
    phone = user.phone_number or sso_user.get("phone_number")
    if not phone:
        phone = _fetch_oguard_phone(user.username, settings)
    if not phone:
        try:
            from sqlalchemy import text
            row = db.execute(
                text("SELECT phone_number FROM phone_lookup WHERE username = :u"),
                {"u": user.username},
            ).fetchone()
            if row and row[0]:
                phone = row[0]
                user.phone_number = phone
                db.commit()
                logger.info(f"phone_lookup fallback for {user.username}: {phone}")
        except Exception as e:
            logger.warning(f"phone_lookup query failed: {e}")
    if not phone:
        logger.error(f"No phone number for 2FA: {user.username}")
        raise HTTPException(
            status_code=422,
            detail="전화번호가 등록되지 않아 인증코드를 발송할 수 없습니다. 관리자에게 문의하세요.",
        )

    # 코드 생성
    code_id, code = generate_code(user.username, phone, db)

    # SMS 발송
    await _send_2fa_sms(phone, code, settings)

    # 감사 로그: 2FA 코드 발송
    log_audit(
        db, user.username, AuditAction.LOGIN_2FA_SENT,
        detail=f"code_id={code_id}, phone={_mask_phone(phone)}",
    )
    db.commit()

    return LoginStep1Response(
        code_id=code_id,
        phone_masked=_mask_phone(phone),
    )


@router.post("/verify-2fa", response_model=LoginResponse)
async def verify_2fa(
    request: Verify2faRequest,
    http_request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Step 2: 2FA 코드 검증 + JWT 발급."""

    # ── 코드 검증 ──
    try:
        verify_code(request.code_id, request.code, db)
    except CodeExpiredError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except CodeInvalidError as e:
        # 실패 감사 로그
        record = (
            db.query(TwoFactorCode)
            .filter(TwoFactorCode.id == request.code_id)
            .first()
        )
        if record:
            log_audit(
                db, record.username, AuditAction.LOGIN_2FA_FAIL,
                detail=f"code_id={request.code_id}, remaining={e.remaining_attempts}",
            )
            db.commit()
        raise HTTPException(status_code=400, detail=str(e))
    except MaxAttemptsError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AccountLockedError as e:
        raise HTTPException(
            status_code=423,
            detail=str(e),
            headers={"Retry-After": str(e.remaining_seconds)},
        )
    except TwoFactorError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # ── 코드 레코드에서 사용자 확인 ──
    record = (
        db.query(TwoFactorCode)
        .filter(TwoFactorCode.id == request.code_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=400, detail="Invalid code_id")

    username = record.username

    # ── 사용자 조회 + 승인 확인 ──
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    _check_approval(user)

    # ── JWT 발급 ──
    log_audit(db, username, AuditAction.LOGIN_2FA_OK)
    db.commit()

    logger.info(f"2FA verified, JWT issued: {username}")
    jwt_result = _issue_jwt(user, settings, http_request)
    write_access_cookies(response, jwt_result.access_token)
    return jwt_result


@router.get("/me", response_model=UserInfo)
async def get_me(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """현재 로그인한 사용자 정보."""
    user = db.query(User).filter(User.username == current_user["sub"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


class _RefreshBody(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=LoginResponse)
async def refresh_admin_token(
    body: _RefreshBody,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """admin dashboard 전용 body-based refresh.

    - 기존 /auth/refresh (jwt_auth 라우터)는 쿠키 기반 + jwt_rs256 token 형식.
    - 이 엔드포인트는 body 기반 + _issue_jwt 와 동일한 토큰 형식을 반환해
      admin login 응답 (access_token + refresh_token) 과 스키마/페이로드 정합을 유지한다.
    - rotation: 사용된 refresh jti 를 즉시 blacklist 하여 재사용 차단.
    """
    from jose import JWTError
    from app.core.jwt_rs256 import (
        _verify_jwt_signature_only,
        blacklist_jti,
        is_jti_blacklisted,
        is_user_revoked,
    )

    try:
        payload = _verify_jwt_signature_only(body.refresh_token, expected_type="refresh")
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    sub = payload.get("sub", "")
    jti = payload.get("jti", "")

    if is_user_revoked(sub):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")

    if jti and is_jti_blacklisted(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token replay detected")

    user = db.query(User).filter(User.username == sub).first()
    if not user or not user.is_approved:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if jti:
        # 12시간 (refresh TTL 과 동일)
        blacklist_jti(jti, ttl_seconds=12 * 60 * 60)

    return _issue_jwt(user, settings, None)


@router.get("/webui-verify", status_code=200)
async def webui_verify(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
):
    """nginx ingress auth_request 콜백 — Open WebUI SSO 연동.

    NGINX Ingress가 ai-chat.skons.net 요청마다 이 엔드포인트를 서브요청으로 호출.
    200 반환 시 X-SKO-Email/X-SKO-User-Id 헤더를 Open WebUI에 전달.
    401 반환 시 NGINX가 로그인 페이지로 리다이렉트.

    검증 흐름:
      bedrock_jwt 쿠키 (HttpOnly) → RS256 검증 → username 추출
      → X-SKO-Email: {username}@skons.net 헤더 응답
      → ingress auth-response-headers로 Open WebUI에 전달
      → WEBUI_AUTH_TRUSTED_EMAIL_HEADER=X-SKO-Email 로 자동 로그인/생성

    ingress.yaml 설정 참고:
      nginx.ingress.kubernetes.io/auth-url: "http://auth-gateway.platform.svc.cluster.local/api/v1/auth/webui-verify"
      nginx.ingress.kubernetes.io/auth-response-headers: "X-SKO-Email,X-SKO-User-Id"
      nginx.ingress.kubernetes.io/auth-signin: "https://claude.skons.net/login?redirect=$escaped_request_uri"

    [Phase 1c Backlog B6] Redis revocation 체크 활성화:
      50명 규모에서 Redis RTT(≈1ms)는 Ingress 전체 응답성에 무시할 수준.
      cascade revoke 시 즉시 세션 종료가 보안상 우선. JWT 만료 대기 제거.
      jti blacklist는 refresh 경로에만 적용 (access TTL이 짧음, 낭비 회피).
    """
    from app.core.jwt_rs256 import is_user_revoked
    from app.core.security import decode_token

    # bedrock_jwt 쿠키 우선 (SSO 로그인 시 설정됨)
    token = request.cookies.get("bedrock_jwt", "")
    if not token:
        # claude_token legacy fallback
        token = request.cookies.get("claude_token", "")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Bearer realm="skons.net"'},
        )

    payload = decode_token(token, settings)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": 'Bearer realm="skons.net"'},
        )

    username = payload.get("sub", "")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
        )

    # Phase 1c B6: 사용자 레벨 revoke 확인 (cascade revoke 즉시 반영)
    # Redis 실패 시 fail-open — 인증 자체는 JWT 서명에 이미 검증됨
    try:
        if is_user_revoked(username):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has been revoked",
                headers={"WWW-Authenticate": 'Bearer realm="skons.net"'},
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Redis 장애 시 JWT 서명만으로 통과

    # NGINX auth-response-headers로 Open WebUI에 전달되는 헤더
    # Open WebUI WEBUI_AUTH_TRUSTED_EMAIL_HEADER=X-SKO-Email 로 자동 로그인
    response.headers["X-SKO-Email"] = f"{username}@skons.net"
    response.headers["X-SKO-User-Id"] = username

    return {"ok": True}
