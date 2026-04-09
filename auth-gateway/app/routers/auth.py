"""인증 API 라우터.

Endpoints:
  POST /api/v1/auth/login       — SSO 로그인 → 2FA 코드 발송 (or 2FA 비활성 → JWT)
  POST /api/v1/auth/verify-2fa  — 2FA 코드 검증 → JWT 발급
  POST /api/v1/auth/logout      — 로그아웃
  GET  /api/v1/auth/me          — 현재 사용자 정보
"""

import logging
from datetime import datetime, timezone
from typing import Union

import httpx
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import create_access_token, get_current_user
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
    """JWT 토큰 발급 + LoginResponse 반환.

    Admin dashboard(claude-admin.skons.net)에서 요청 시 admin role 필수.
    """
    # Admin dashboard Origin 체크: admin이 아니면 로그인 거부
    if request:
        origin = request.headers.get("Origin", "") or request.headers.get("Referer", "")
        if "claude-admin" in origin and user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="관리자 계정만 Admin Dashboard에 로그인할 수 있습니다.",
            )

    token_data = {
        "sub": user.username,
        "user_id": user.id,
        "role": user.role,
    }
    access_token = create_access_token(token_data, settings)
    return LoginResponse(
        access_token=access_token,
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
            return _issue_jwt(user, settings, http_request)

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
        return _issue_jwt(user, settings, http_request)

    # ── 테스트 계정 2FA 우회 ──
    # SECURITY: allow_test_users=True (기본값 False)일 때만 활성화됩니다.
    # 프로덕션 환경에서는 ALLOW_TEST_USERS 환경변수를 설정하지 마십시오.
    if settings.allow_test_users and user.username.startswith("TEST"):
        log_audit(db, user.username, AuditAction.LOGIN_BYPASS, detail="test account 2FA skip")
        db.commit()
        logger.info(f"Test account 2FA bypass: {user.username}")
        return _issue_jwt(user, settings, http_request)

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
    return _issue_jwt(user, settings, http_request)


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
