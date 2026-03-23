"""공유 스킬 관리 API 라우터.

사용자가 Pod에서 만든 스킬/프롬프트를 중앙에 제출하고,
관리자가 검토/승인 후 전체 사용자에게 배포.

Endpoints:
  POST   /api/v1/skills/submit         — 스킬 제출 (인증 사용자)
  GET    /api/v1/skills/               — 승인된 스킬 목록 (인증 사용자)
  GET    /api/v1/skills/pending        — 검토 대기 목록 (관리자)
  PATCH  /api/v1/skills/{id}/approve   — 스킬 승인 (관리자)
  DELETE /api/v1/skills/{id}           — 스킬 삭제/거절 (관리자)
  GET    /api/v1/skills/approved-contents — 승인된 스킬 내용 (Pod init용, 인증 불필요)
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.skill import SharedSkill
from app.models.user import User
from app.schemas.skill import SkillListResponse, SkillResponse, SkillSubmitRequest

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])
logger = logging.getLogger(__name__)


# ==================== Helper ====================


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """관리자 권한 확인."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ==================== 사용자 API ====================


@router.post("/submit", response_model=SkillResponse, status_code=201)
async def submit_skill(
    request: SkillSubmitRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """스킬 제출 (모든 인증 사용자).

    제출된 스킬은 is_approved=False 상태로 저장되며,
    관리자 승인 후 전체 사용자에게 배포됨.
    """
    user = db.query(User).filter(User.username == current_user["sub"]).first()

    skill = SharedSkill(
        author_username=current_user["sub"],
        author_name=user.name if user else current_user["sub"],
        title=request.title,
        description=request.description,
        category=request.category,
        content=request.content,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    logger.info(f"Skill submitted: {skill.title} by {skill.author_username}")
    return skill


@router.get("/", response_model=SkillListResponse)
async def list_approved_skills(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """승인된 스킬 목록 (모든 인증 사용자).

    usage_count 내림차순으로 정렬하여 인기 스킬이 먼저 표시됨.
    """
    skills = (
        db.query(SharedSkill)
        .filter(SharedSkill.is_approved == True)  # noqa: E712
        .order_by(SharedSkill.usage_count.desc())
        .all()
    )
    return SkillListResponse(total=len(skills), skills=skills)


# ==================== 관리자 API ====================


@router.get("/pending", response_model=SkillListResponse)
async def list_pending_skills(
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """검토 대기 스킬 목록 (관리자).

    최신 제출순으로 정렬.
    """
    skills = (
        db.query(SharedSkill)
        .filter(SharedSkill.is_approved == False)  # noqa: E712
        .order_by(SharedSkill.created_at.desc())
        .all()
    )
    return SkillListResponse(total=len(skills), skills=skills)


@router.patch("/{skill_id}/approve", response_model=SkillResponse)
async def approve_skill(
    skill_id: int,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """스킬 승인 (관리자).

    승인된 스킬은 /approved-contents 엔드포인트를 통해
    새 Pod 생성 시 자동으로 배포됨.
    """
    skill = db.query(SharedSkill).filter(SharedSkill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    skill.is_approved = True
    skill.approved_by = admin["sub"]
    skill.approved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(skill)
    logger.info(f"Skill approved: {skill.title} by {admin['sub']}")
    return skill


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: int,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """스킬 삭제/거절 (관리자).

    승인 전 거절 또는 부적절한 스킬 삭제에 사용.
    """
    skill = db.query(SharedSkill).filter(SharedSkill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    title = skill.title
    db.delete(skill)
    db.commit()
    logger.info(f"Skill deleted: {title}")
    return {"deleted": True, "title": title}


# ==================== Pod Init API (인증 불필요) ====================


@router.get("/approved-contents")
async def get_approved_skill_contents(db: Session = Depends(get_db)):
    """승인된 스킬 내용 반환 (Pod init 스크립트에서 호출).

    인증 불필요 — Pod 시작 시 init 컨테이너가 이 엔드포인트를 호출하여
    승인된 스킬을 사용자 환경에 자동 배포함.

    Returns:
        list[dict]: 각 스킬의 name(파일명용)과 content.
    """
    skills = (
        db.query(SharedSkill)
        .filter(SharedSkill.is_approved == True)  # noqa: E712
        .all()
    )
    return [
        {"name": s.title.lower().replace(" ", "-"), "content": s.content}
        for s in skills
    ]
