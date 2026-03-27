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

from pydantic import BaseModel

from app.core.config import Settings, get_settings
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
    if user.username == _admin.get("sub"):
        raise HTTPException(status_code=400, detail="관리자 본인의 승인을 취소할 수 없습니다")
    if user.role == "admin":
        raise HTTPException(status_code=400, detail="관리자 계정의 승인을 취소할 수 없습니다")

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


# ==================== 구성원 검색 + 직접 추가 ====================

class OGuardProfile(BaseModel):
    username: str
    first_name: str | None = None
    region_name: str | None = None
    team_name: str | None = None
    job_name: str | None = None

class OGuardSearchResponse(BaseModel):
    total: int
    results: list[OGuardProfile]


@router.get("/search-members", response_model=OGuardSearchResponse)
async def search_members(
    q: str,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """O-Guard safety DB에서 구성원 검색 (사번 또는 이름으로)."""
    import psycopg2
    import psycopg2.extras

    if not settings.workshop_database_url:
        raise HTTPException(status_code=500, detail="Workshop DB not configured")

    try:
        conn = psycopg2.connect(settings.workshop_database_url)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT u.username, u.first_name, p.region_name, p.team_name, p.job_name
            FROM auth_user u
            JOIN accounts_userprofile p ON u.id = p.user_id
            WHERE u.username ILIKE %s OR u.first_name ILIKE %s
            ORDER BY u.username
            LIMIT 20
        """, (f"%{q}%", f"%{q}%"))
        rows = cur.fetchall()
        conn.close()
        return OGuardSearchResponse(
            total=len(rows),
            results=[OGuardProfile(**row) for row in rows],
        )
    except Exception as e:
        logger.error(f"Member search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class DirectAddRequest(BaseModel):
    username: str
    pod_ttl: str = "4h"


@router.post("/add-member", response_model=UserResponse)
async def add_member_directly(
    req: DirectAddRequest,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """구성원을 직접 허용목록에 추가 (승인 완료 상태)."""
    existing = db.query(User).filter(User.username == req.username.upper()).first()
    if existing:
        if existing.is_approved:
            raise HTTPException(status_code=400, detail="이미 승인된 사용자입니다")
        existing.is_approved = True
        existing.pod_ttl = req.pod_ttl
        existing.approved_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        logger.info(f"Existing user {existing.username} directly approved")
        return UserResponse.model_validate(existing)

    # O-Guard에서 프로필 조회
    import psycopg2
    import psycopg2.extras
    profile = {}
    if settings.workshop_database_url:
        try:
            conn = psycopg2.connect(settings.workshop_database_url)
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT u.first_name, p.region_name, p.team_name, p.job_name
                FROM auth_user u
                JOIN accounts_userprofile p ON u.id = p.user_id
                WHERE u.username = %s
            """, (req.username.upper(),))
            row = cur.fetchone()
            conn.close()
            if row:
                profile = dict(row)
        except Exception as e:
            logger.warning(f"O-Guard profile fetch failed: {e}")

    user = User(
        username=req.username.upper(),
        name=profile.get("first_name"),
        region_name=profile.get("region_name"),
        team_name=profile.get("team_name"),
        job_name=profile.get("job_name"),
        role="user",
        is_approved=True,
        pod_ttl=req.pod_ttl,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info(f"Member {user.username} ({user.name}) directly added and approved")
    return UserResponse.model_validate(user)
