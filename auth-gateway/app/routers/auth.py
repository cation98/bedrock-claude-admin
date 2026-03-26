"""인증 API 라우터.

Endpoints:
  POST /api/v1/auth/login   — SSO 로그인 → JWT 발급
  POST /api/v1/auth/logout  — 로그아웃
  GET  /api/v1/auth/me      — 현재 사용자 정보
"""

import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import create_access_token, get_current_user
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserInfo
from app.services.sso_service import SSOService, SSOAuthError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
logger = logging.getLogger(__name__)


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


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """SSO 로그인.

    1. sso.skons.net에서 사내 구성원 인증
    2. 플랫폼 DB에 사용자 등록/업데이트
    3. JWT 토큰 발급
    """
    sso_service = SSOService(settings)

    try:
        sso_user = await sso_service.authenticate(request.username, request.password)
    except SSOAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"SSO authentication failed: {e.message}",
        )

    # O-Guard safety DB에서 사용자 프로필 조회
    profile = _fetch_oguard_profile(sso_user["username"], settings)

    # DB에 사용자 등록/업데이트
    user = db.query(User).filter(User.username == sso_user["username"]).first()
    is_new_user = False
    if not user:
        is_new_user = True
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
        # 프로필 정보가 있으면 업데이트
        if profile:
            user.name = profile.get("first_name") or user.name
            user.region_name = profile.get("region_name") or user.region_name
            user.team_name = profile.get("team_name") or user.team_name
            user.job_name = profile.get("job_name") or user.job_name
        user.phone_number = sso_user.get("phone_number") or user.phone_number

    user.last_login_at = datetime.now(timezone.utc)

    # 로그인 시 자동 승인 (워크숍 모드)
    if not user.is_approved:
        user.is_approved = True
        user.approved_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(user)

    # 승인 여부 확인 — 현재 자동 승인 모드 (아래 코드는 비활성)
    if False and not user.is_approved:
        logger.info(f"Unapproved user login attempt: {user.username} (new={is_new_user})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "approval_pending",
                "message": "관리자 승인이 필요합니다. 승인 후 다시 로그인해주세요.",
            },
        )

    # JWT 발급
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
