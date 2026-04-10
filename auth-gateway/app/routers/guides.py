"""가이드 콘텐츠 API 라우터.

사용자가 명령어·활용 가이드를 작성하여 제출하고,
관리자 승인 후 전체 사용자에게 공개.

Endpoints:
  GET    /api/v1/guides/           — 공개된 가이드 목록 (인증 사용자, category 필터 가능)
  GET    /api/v1/guides/{id}       — 가이드 상세 조회 (조회수 증가)
  POST   /api/v1/guides/           — 가이드 작성 (인증 사용자, 관리자 승인 필요)
  PUT    /api/v1/guides/{id}       — 가이드 수정 (작성자 또는 관리자)
  POST   /api/v1/guides/{id}/publish — 가이드 공개 승인 (관리자)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.guide import Guide
from app.models.user import User

router = APIRouter(prefix="/api/v1/guides", tags=["guides"])
logger = logging.getLogger(__name__)


# ==================== Helper ====================


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """관리자 권한 확인."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return current_user


def _get_guide_or_404(guide_id: int, db: Session) -> Guide:
    """가이드 조회 또는 404 반환."""
    guide = db.query(Guide).filter(Guide.id == guide_id).first()
    if not guide:
        raise HTTPException(status_code=404, detail="가이드를 찾을 수 없습니다")
    return guide


def _get_author_name(author_username: str, db: Session) -> str:
    """작성자 이름 조회 (없으면 username 반환)."""
    user = db.query(User).filter(User.username == author_username).first()
    return user.name if user else author_username


# ==================== 사용자 API ====================


@router.get("/")
async def list_guides(
    category: str = Query(None, description="카테고리 필터 (general, command, workflow, tip)"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """공개된 가이드 목록 조회 (모든 인증 사용자).

    최신순 정렬. category 파라미터로 필터링 가능.
    작성자 이름 포함 (User 테이블 JOIN).
    """
    query = db.query(Guide).filter(Guide.is_published == True)  # noqa: E712

    if category:
        query = query.filter(Guide.category == category)

    guides = query.order_by(Guide.created_at.desc()).all()

    # Batch-load authors to avoid N+1 queries
    author_usernames = list(set(g.author_username for g in guides))
    authors = db.query(User).filter(User.username.in_(author_usernames)).all() if author_usernames else []
    authors_map = {u.username: u for u in authors}

    results = []
    for g in guides:
        author = authors_map.get(g.author_username)
        results.append({
            "id": g.id,
            "title": g.title,
            "category": g.category,
            "author_username": g.author_username,
            "author_name": author.name if author else g.author_username,
            "view_count": g.view_count,
            "created_at": g.created_at.isoformat() if g.created_at else None,
        })

    return {"guides": results}


@router.get("/{guide_id}")
async def get_guide(
    guide_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """가이드 상세 조회 (조회수 증가).

    공개된 가이드는 모든 인증 사용자가 조회 가능.
    관리자는 미공개 가이드도 조회 가능.
    """
    guide = _get_guide_or_404(guide_id, db)

    is_admin = current_user.get("role") == "admin"
    is_author = guide.author_username == current_user["sub"]

    if not guide.is_published and not is_admin and not is_author:
        raise HTTPException(status_code=403, detail="공개되지 않은 가이드입니다")

    # 조회수 증가
    guide.view_count = (guide.view_count or 0) + 1
    db.commit()
    db.refresh(guide)

    author_name = _get_author_name(guide.author_username, db)

    return {
        "id": guide.id,
        "title": guide.title,
        "content": guide.content,
        "category": guide.category,
        "author_username": guide.author_username,
        "author_name": author_name,
        "is_published": guide.is_published,
        "view_count": guide.view_count,
        "created_at": guide.created_at.isoformat() if guide.created_at else None,
        "updated_at": guide.updated_at.isoformat() if guide.updated_at else None,
    }


@router.post("/", status_code=201)
async def create_guide(
    request: dict,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """가이드 작성 (모든 인증 사용자).

    작성된 가이드는 is_published=False 상태로 저장되며,
    관리자 승인 후 전체 사용자에게 공개됨.
    """
    title = request.get("title", "").strip()
    if not title or len(title) > 200:
        raise HTTPException(status_code=400, detail="제목은 1-200자여야 합니다")

    content = request.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="내용을 입력해 주세요")

    category = request.get("category", "general")
    valid_categories = ("general", "command", "workflow", "tip")
    if category not in valid_categories:
        raise HTTPException(
            status_code=400,
            detail=f"카테고리는 {', '.join(valid_categories)} 중 하나여야 합니다",
        )

    guide = Guide(
        title=title,
        content=content,
        category=category,
        author_username=current_user["sub"],
        is_published=False,
    )
    db.add(guide)
    db.commit()
    db.refresh(guide)
    logger.info(f"Guide created: '{guide.title}' by {guide.author_username}")

    return {
        "id": guide.id,
        "title": guide.title,
        "category": guide.category,
        "is_published": guide.is_published,
        "created_at": guide.created_at.isoformat() if guide.created_at else None,
    }


@router.put("/{guide_id}")
async def update_guide(
    guide_id: int,
    request: dict,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """가이드 수정 (작성자 또는 관리자).

    공개된 가이드를 수정하면 is_published=False로 재설정되어 재승인이 필요.
    관리자는 is_published 상태를 유지하며 수정 가능.
    """
    guide = _get_guide_or_404(guide_id, db)

    is_admin = current_user.get("role") == "admin"
    is_author = guide.author_username == current_user["sub"]

    if not is_admin and not is_author:
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다")

    if "title" in request:
        title = request["title"].strip()
        if not title or len(title) > 200:
            raise HTTPException(status_code=400, detail="제목은 1-200자여야 합니다")
        guide.title = title

    if "content" in request:
        content = request["content"].strip()
        if not content:
            raise HTTPException(status_code=400, detail="내용을 입력해 주세요")
        guide.content = content

    if "category" in request:
        category = request["category"]
        valid_categories = ("general", "command", "workflow", "tip")
        if category not in valid_categories:
            raise HTTPException(
                status_code=400,
                detail=f"카테고리는 {', '.join(valid_categories)} 중 하나여야 합니다",
            )
        guide.category = category

    # 작성자가 수정하면 재승인 필요 (관리자 직접 수정 시 공개 상태 유지)
    if not is_admin and guide.is_published:
        guide.is_published = False
        logger.info(f"Guide unpublished for re-review: id={guide_id}")

    db.commit()
    db.refresh(guide)
    logger.info(f"Guide updated: id={guide_id} by {current_user['sub']}")

    author_name = _get_author_name(guide.author_username, db)

    return {
        "id": guide.id,
        "title": guide.title,
        "category": guide.category,
        "author_username": guide.author_username,
        "author_name": author_name,
        "is_published": guide.is_published,
        "updated_at": guide.updated_at.isoformat() if guide.updated_at else None,
    }


# ==================== 관리자 API ====================


@router.post("/{guide_id}/publish")
async def publish_guide(
    guide_id: int,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """가이드 공개 승인 (관리자).

    승인된 가이드는 전체 사용자에게 공개됨.
    """
    guide = _get_guide_or_404(guide_id, db)

    if guide.is_published:
        raise HTTPException(status_code=409, detail="이미 공개된 가이드입니다")

    guide.is_published = True
    db.commit()
    db.refresh(guide)
    logger.info(f"Guide published: id={guide_id} by {admin['sub']}")

    return {
        "id": guide.id,
        "title": guide.title,
        "is_published": guide.is_published,
        "published_by": admin["sub"],
    }
