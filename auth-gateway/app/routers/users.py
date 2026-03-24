"""사용자 관리 API 라우터 (관리자 전용).

Endpoints:
  GET    /api/v1/users/                    -- 전체 사용자 목록
  GET    /api/v1/users/pending             -- 승인 대기 사용자 목록
  PATCH  /api/v1/users/{user_id}/approve   -- 사용자 승인 (is_approved=True)
  PATCH  /api/v1/users/{user_id}/ttl       -- Pod TTL 변경
  DELETE /api/v1/users/{user_id}/approve   -- 사용자 승인 취소 (is_approved=False)
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.user import (
    ApproveRequest,
    TTLUpdateRequest,
    UserListResponse,
    UserResponse,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])
logger = logging.getLogger(__name__)


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """관리자 권한 확인."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ==================== 관리자 API ====================


@router.get("/", response_model=UserListResponse)
async def list_users(
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """전체 사용자 목록 (관리자용)."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return UserListResponse(
        total=len(users),
        users=[UserResponse.model_validate(u) for u in users],
    )


@router.get("/pending", response_model=UserListResponse)
async def list_pending_users(
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """승인 대기 사용자 목록 (관리자용)."""
    users = (
        db.query(User)
        .filter(User.is_approved == False)  # noqa: E712
        .order_by(User.created_at.desc())
        .all()
    )
    return UserListResponse(
        total=len(users),
        users=[UserResponse.model_validate(u) for u in users],
    )


@router.patch("/{user_id}/approve", response_model=UserResponse)
async def approve_user(
    user_id: int,
    request: ApproveRequest,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """사용자 승인 (관리자용).

    승인 시 is_approved=True, pod_ttl 설정, approved_at 기록.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_approved = True
    user.pod_ttl = request.pod_ttl
    user.approved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    logger.info(f"User {user.username} approved with pod_ttl={request.pod_ttl}")
    return UserResponse.model_validate(user)


@router.patch("/{user_id}/ttl", response_model=UserResponse)
async def update_user_ttl(
    user_id: int,
    request: TTLUpdateRequest,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """사용자 Pod TTL 변경 (관리자용)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.pod_ttl = request.pod_ttl
    db.commit()
    db.refresh(user)

    logger.info(f"User {user.username} pod_ttl updated to {request.pod_ttl}")
    return UserResponse.model_validate(user)


@router.delete("/{user_id}/approve", response_model=UserResponse)
async def revoke_approval(
    user_id: int,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """사용자 승인 취소 (관리자용)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_approved = False
    db.commit()
    db.refresh(user)

    logger.info(f"User {user.username} approval revoked")
    return UserResponse.model_validate(user)


@router.delete("/{user_id}", status_code=200)
async def reject_user(
    user_id: int,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """사용자 거절 — DB에서 삭제 (관리자용)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_approved:
        raise HTTPException(status_code=400, detail="승인된 사용자는 거절할 수 없습니다. 승인 취소를 먼저 하세요.")

    username = user.username
    db.delete(user)
    db.commit()
    logger.info(f"User {username} rejected and deleted")
    return {"deleted": True, "username": username}
