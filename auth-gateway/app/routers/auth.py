"""인증 API 라우터.

Endpoints:
  POST /api/v1/auth/login   — SSO 로그인 → JWT 발급
  POST /api/v1/auth/logout  — 로그아웃
  GET  /api/v1/auth/me      — 현재 사용자 정보
"""

import logging
from datetime import datetime, timezone

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

    # DB에 사용자 등록/업데이트
    user = db.query(User).filter(User.username == sso_user["username"]).first()
    is_new_user = False
    if not user:
        is_new_user = True
        user = User(
            username=sso_user["username"],
            name=sso_user.get("name"),
            phone_number=sso_user.get("phone_number"),
        )
        db.add(user)
    else:
        user.name = sso_user.get("name") or user.name
        user.phone_number = sso_user.get("phone_number") or user.phone_number

    user.last_login_at = datetime.now(timezone.utc)

    # 관리자는 자동 승인 (최초 등록 시에도 즉시 사용 가능)
    if user.role == "admin" and not user.is_approved:
        user.is_approved = True
        user.approved_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(user)

    # 승인 여부 확인 — 미승인 사용자는 로그인 차단
    if not user.is_approved:
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
