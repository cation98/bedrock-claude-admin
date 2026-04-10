"""공지사항 관리 API.

Endpoints:
  GET  /api/v1/announcements/active   -- 활성 공지 목록 (모든 인증 사용자)
  GET  /api/v1/announcements          -- 전체 공지 목록 (관리자)
  POST /api/v1/announcements          -- 공지 작성 (관리자)
  PUT  /api/v1/announcements/{id}     -- 공지 수정 (관리자)
  DELETE /api/v1/announcements/{id}   -- 공지 삭제 (관리자)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.announcement import Announcement

router = APIRouter(prefix="/api/v1/announcements", tags=["announcements"])
logger = logging.getLogger(__name__)


# ==================== Pydantic 스키마 ====================


class AnnouncementCreate(BaseModel):
    title: str
    content: str
    is_pinned: bool = False
    expires_at: Optional[str] = None  # ISO 8601 문자열 또는 null


class AnnouncementUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    is_active: Optional[bool] = None
    is_pinned: Optional[bool] = None
    expires_at: Optional[str] = None  # ISO 8601 문자열 또는 null (빈 문자열 = 만료 해제)


# ==================== 고정 경로 먼저 (동적 경로 /{id} 보다 앞) ====================


@router.get("/active")
async def list_active_announcements(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """활성 공지 목록 — 모든 인증 사용자가 조회 가능.

    - is_active=True
    - expires_at IS NULL 또는 expires_at > 현재 시각
    - 고정 공지(is_pinned) 우선, 최신순 정렬
    """
    now = datetime.now(timezone.utc)
    announcements = (
        db.query(Announcement)
        .filter(
            Announcement.is_active == True,  # noqa: E712
            (Announcement.expires_at == None) | (Announcement.expires_at > now),  # noqa: E711
        )
        .order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc())
        .all()
    )

    return {
        "announcements": [
            {
                "id": a.id,
                "title": a.title,
                "content": a.content,
                "author_username": a.author_username,
                "is_pinned": a.is_pinned,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in announcements
        ]
    }


# ==================== 관리자 전용 CRUD ====================


@router.get("")
async def list_all_announcements(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """전체 공지 목록 — 관리자 전용."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다")

    announcements = (
        db.query(Announcement)
        .order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc())
        .all()
    )

    return {
        "announcements": [
            {
                "id": a.id,
                "title": a.title,
                "content": a.content,
                "author_username": a.author_username,
                "is_active": a.is_active,
                "is_pinned": a.is_pinned,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
                "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            }
            for a in announcements
        ]
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_announcement(
    body: AnnouncementCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """공지 작성 — 관리자 전용."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다")

    expires_at = None
    if body.expires_at:
        try:
            expires_at = datetime.fromisoformat(body.expires_at)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="expires_at 형식이 올바르지 않습니다 (ISO 8601)")

    announcement = Announcement(
        title=body.title,
        content=body.content,
        author_username=current_user["sub"],
        is_pinned=body.is_pinned,
        expires_at=expires_at,
    )
    db.add(announcement)
    db.commit()
    db.refresh(announcement)

    logger.info("공지사항 생성: id=%s by %s", announcement.id, current_user["sub"])
    return {
        "id": announcement.id,
        "title": announcement.title,
        "content": announcement.content,
        "author_username": announcement.author_username,
        "is_active": announcement.is_active,
        "is_pinned": announcement.is_pinned,
        "created_at": announcement.created_at.isoformat() if announcement.created_at else None,
        "expires_at": announcement.expires_at.isoformat() if announcement.expires_at else None,
    }


@router.put("/{announcement_id}")
async def update_announcement(
    announcement_id: int,
    body: AnnouncementUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """공지 수정 — 관리자 전용."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다")

    announcement = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="공지사항을 찾을 수 없습니다")

    if body.title is not None:
        announcement.title = body.title
    if body.content is not None:
        announcement.content = body.content
    if body.is_active is not None:
        announcement.is_active = body.is_active
    if body.is_pinned is not None:
        announcement.is_pinned = body.is_pinned
    if body.expires_at is not None:
        if body.expires_at == "":
            announcement.expires_at = None  # 만료 해제
        else:
            try:
                parsed_dt = datetime.fromisoformat(body.expires_at)
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                announcement.expires_at = parsed_dt
            except ValueError:
                raise HTTPException(status_code=400, detail="expires_at 형식이 올바르지 않습니다 (ISO 8601)")

    announcement.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(announcement)

    logger.info("공지사항 수정: id=%s by %s", announcement_id, current_user["sub"])
    return {
        "id": announcement.id,
        "title": announcement.title,
        "content": announcement.content,
        "author_username": announcement.author_username,
        "is_active": announcement.is_active,
        "is_pinned": announcement.is_pinned,
        "created_at": announcement.created_at.isoformat() if announcement.created_at else None,
        "updated_at": announcement.updated_at.isoformat() if announcement.updated_at else None,
        "expires_at": announcement.expires_at.isoformat() if announcement.expires_at else None,
    }


@router.delete("/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_announcement(
    announcement_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """공지 삭제 (hard delete) — 관리자 전용."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다")

    announcement = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="공지사항을 찾을 수 없습니다")

    db.delete(announcement)
    db.commit()
    logger.info("공지사항 삭제: id=%s by %s", announcement_id, current_user["sub"])
