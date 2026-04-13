"""공유 스킬 관리 API 라우터.

사용자가 Pod에서 만든 스킬/프롬프트를 중앙에 제출하고,
관리자가 검토/승인 후 전체 사용자에게 배포.

Endpoints:
  POST   /api/v1/skills/submit         — 스킬 제출 (인증 사용자)
  GET    /api/v1/skills/               — 승인된 스킬 목록 (인증 사용자)
  GET    /api/v1/skills/pending        — 검토 대기 목록 (관리자)
  GET    /api/v1/skills/recommended    — 사용자 맞춤 스킬 추천 (인증 사용자)
  PATCH  /api/v1/skills/{id}/approve   — 스킬 승인 (관리자)
  DELETE /api/v1/skills/{id}           — 스킬 삭제/거절 (관리자)
  GET    /api/v1/skills/approved-contents — 승인된 스킬 내용 (Pod init용, 인증 불필요)
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.skill import SharedSkill, SkillInstall
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


def _skill_to_dict(s: SharedSkill) -> dict:
    """SharedSkill 모델을 추천 응답용 dict로 변환."""
    return {
        "id": s.id,
        "name": s.display_name or s.skill_name,
        "description": s.description,
        "author": s.owner_username,
        "installs": s.install_count,
        "skill_type": s.skill_type,
    }


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


@router.get("/store")
async def list_store_skills(
    q: str = "",
    sort: str = "popular",
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """스킬 스토어 목록 (인기순/최신순, 검색)."""
    query = db.query(SharedSkill).filter(SharedSkill.is_active == True)  # noqa: E712

    if q:
        query = query.filter(
            SharedSkill.skill_name.ilike(f"%{q}%")
            | SharedSkill.display_name.ilike(f"%{q}%")
            | SharedSkill.description.ilike(f"%{q}%")
        )

    if sort == "popular":
        query = query.order_by(SharedSkill.install_count.desc())
    else:
        query = query.order_by(SharedSkill.created_at.desc())

    skills = query.limit(50).all()

    # Check which ones the user has installed
    username = current_user["sub"]
    installed_ids = set(
        r[0] for r in db.query(SkillInstall.skill_id)
        .filter(SkillInstall.username == username, SkillInstall.uninstalled_at.is_(None))
        .all()
    )

    # Batch-load owners to avoid N+1 queries
    owner_usernames = list(set(s.owner_username for s in skills))
    owners = db.query(User).filter(User.username.in_(owner_usernames)).all() if owner_usernames else []
    owners_map = {u.username: u for u in owners}

    results = []
    for s in skills:
        owner = owners_map.get(s.owner_username)
        results.append({
            "id": s.id,
            "skill_name": s.skill_name,
            "display_name": s.display_name or s.skill_name,
            "description": s.description or "",
            "skill_type": s.skill_type,
            "owner_username": s.owner_username,
            "owner_name": owner.name if owner else s.owner_username,
            "install_count": s.install_count,
            "is_installed": s.id in installed_ids,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })

    return {"skills": results}


@router.get("/my")
async def list_my_skills(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """내가 공유한 스킬 목록."""
    username = current_user["sub"]
    skills = (
        db.query(SharedSkill)
        .filter(SharedSkill.owner_username == username)
        .order_by(SharedSkill.created_at.desc())
        .all()
    )
    return {"skills": [{
        "id": s.id,
        "skill_name": s.skill_name,
        "display_name": s.display_name,
        "description": s.description,
        "skill_type": s.skill_type,
        "skill_dir_name": s.skill_dir_name,
        "install_count": s.install_count,
        "is_active": s.is_active,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    } for s in skills]}


@router.get("/installed")
async def list_installed_skills(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """설치한 스킬 목록."""
    username = current_user["sub"]
    installs = (
        db.query(SkillInstall, SharedSkill)
        .join(SharedSkill, SkillInstall.skill_id == SharedSkill.id)
        .filter(SkillInstall.username == username, SkillInstall.uninstalled_at.is_(None))
        .order_by(SkillInstall.installed_at.desc())
        .all()
    )

    # Batch-load owners to avoid N+1 queries
    owner_usernames = list(set(skill.owner_username for _, skill in installs))
    owners = db.query(User).filter(User.username.in_(owner_usernames)).all() if owner_usernames else []
    owners_map = {u.username: u for u in owners}

    results = []
    for install, skill in installs:
        owner = owners_map.get(skill.owner_username)
        results.append({
            "install_id": install.id,
            "skill_id": skill.id,
            "skill_name": skill.skill_name,
            "display_name": skill.display_name or skill.skill_name,
            "description": skill.description,
            "owner_username": skill.owner_username,
            "owner_name": owner.name if owner else skill.owner_username,
            "installed_at": install.installed_at.isoformat() if install.installed_at else None,
        })

    return {"skills": results}


@router.post("/publish")
async def publish_skill(
    request: dict,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """스킬을 스토어에 공유."""
    username = current_user["sub"]

    # Input validation
    skill_name = request.get("skill_name", "").strip()
    if not skill_name or len(skill_name) > 100:
        raise HTTPException(status_code=400, detail="스킬 이름은 1-100자여야 합니다")

    skill_type = request.get("skill_type", "slash_command")
    if skill_type not in ("slash_command", "workflow"):
        raise HTTPException(status_code=400, detail="skill_type은 slash_command 또는 workflow여야 합니다")

    dir_name = request.get("dir_name", "").strip()
    if not dir_name or len(dir_name) > 100:
        raise HTTPException(status_code=400, detail="dir_name은 1-100자여야 합니다")

    # Check if already published
    existing = (
        db.query(SharedSkill)
        .filter(SharedSkill.owner_username == username, SharedSkill.skill_dir_name == request.get("dir_name"))
        .first()
    )
    if existing:
        # Update
        existing.skill_name = request.get("skill_name", existing.skill_name)
        existing.display_name = request.get("display_name", existing.display_name)
        existing.description = request.get("description", existing.description)
        existing.skill_type = request.get("skill_type", existing.skill_type)
        existing.is_active = True
        db.commit()
        return {"id": existing.id, "updated": True}

    skill = SharedSkill(
        owner_username=username,
        skill_name=request.get("skill_name", ""),
        display_name=request.get("display_name", ""),
        description=request.get("description", ""),
        skill_type=request.get("skill_type", "slash_command"),
        skill_dir_name=request.get("dir_name", ""),
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return {"id": skill.id, "published": True}


@router.get("/recommended")
async def get_recommended_skills(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """사용자 맞춤 스킬 추천 (설치 이력 기반 collaborative filtering).

    1. 이 사용자가 설치한 스킬 목록 조회
    2. 같은 스킬을 설치한 다른 사용자 찾기
    3. 그 사용자들이 설치했지만 나는 안 한 스킬 -> 추천
    4. install_count 높은 순으로 정렬
    """
    username = current_user["sub"]

    # 내가 설치한 스킬 ID
    my_installs = db.query(SkillInstall.skill_id).filter(
        SkillInstall.username == username,
        SkillInstall.uninstalled_at.is_(None),
    ).all()
    my_skill_ids = {r[0] for r in my_installs}

    if not my_skill_ids:
        # 설치 이력 없으면 인기순 top 5
        popular = (
            db.query(SharedSkill)
            .filter(SharedSkill.is_active == True)  # noqa: E712
            .order_by(SharedSkill.install_count.desc())
            .limit(5)
            .all()
        )
        return {"skills": [_skill_to_dict(s) for s in popular], "reason": "popular"}

    # 같은 스킬 설치한 다른 사용자
    similar_users = db.query(SkillInstall.username).filter(
        SkillInstall.skill_id.in_(my_skill_ids),
        SkillInstall.username != username,
        SkillInstall.uninstalled_at.is_(None),
    ).distinct().all()
    similar_usernames = {r[0] for r in similar_users}

    if not similar_usernames:
        # 유사 사용자 없으면 내가 설치하지 않은 인기순 top 5
        popular = (
            db.query(SharedSkill)
            .filter(
                SharedSkill.is_active == True,  # noqa: E712
                ~SharedSkill.id.in_(my_skill_ids),
            )
            .order_by(SharedSkill.install_count.desc())
            .limit(5)
            .all()
        )
        return {"skills": [_skill_to_dict(s) for s in popular], "reason": "popular"}

    # 그들이 설치한 스킬 중 내가 안 한 것 (공동 설치 횟수 기준 정렬)
    recommended_ids = (
        db.query(
            SkillInstall.skill_id,
            func.count(SkillInstall.id).label("cnt"),
        )
        .filter(
            SkillInstall.username.in_(similar_usernames),
            ~SkillInstall.skill_id.in_(my_skill_ids),
            SkillInstall.uninstalled_at.is_(None),
        )
        .group_by(SkillInstall.skill_id)
        .order_by(func.count(SkillInstall.id).desc())
        .limit(5)
        .all()
    )

    skill_ids = [r[0] for r in recommended_ids]
    if skill_ids:
        skills = (
            db.query(SharedSkill)
            .filter(SharedSkill.id.in_(skill_ids), SharedSkill.is_active == True)  # noqa: E712
            .all()
        )
        # 공동 설치 횟수 순서 유지
        skill_map = {s.id: s for s in skills}
        ordered = [skill_map[sid] for sid in skill_ids if sid in skill_map]
        return {"skills": [_skill_to_dict(s) for s in ordered], "reason": "collaborative"}

    return {"skills": [], "reason": "none"}


@router.patch("/{skill_id}/approve", response_model=SkillResponse)
async def approve_skill(
    skill_id: int,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """스킬 승인 (관리자).

    SoD(Separation of Duties): 스킬 작성자는 자신이 제출한 스킬을 승인할 수
    없다. 4-eyes 원칙 위반 시 403 반환. author_username 또는 owner_username 중
    하나라도 admin 본인 사번과 일치하면 거부.

    승인된 스킬은 /approved-contents 엔드포인트를 통해
    새 Pod 생성 시 자동으로 배포됨.
    """
    skill = db.query(SharedSkill).filter(SharedSkill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # SoD: 자기 제출 스킬은 다른 관리자가 승인해야 함 (4-eyes 원칙)
    admin_sub = admin["sub"]
    if skill.author_username == admin_sub or skill.owner_username == admin_sub:
        logger.warning(
            "SoD violation blocked: admin=%s attempted self-approval on skill=%s (author=%s owner=%s)",
            admin_sub, skill_id, skill.author_username, skill.owner_username,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "sod_violation",
                "message": "자신이 제출한 스킬은 승인할 수 없습니다. 다른 관리자에게 승인을 요청하세요.",
            },
        )

    skill.is_approved = True
    skill.approved_by = admin_sub
    skill.approved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(skill)
    logger.info(f"Skill approved: {skill.title} by {admin_sub}")
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


# ==================== 스킬 스토어 API ====================


@router.post("/{skill_id}/install")
async def install_skill(
    skill_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """스킬 설치."""
    username = current_user["sub"]

    skill = db.query(SharedSkill).filter(SharedSkill.id == skill_id, SharedSkill.is_active == True).first()  # noqa: E712
    if not skill:
        raise HTTPException(status_code=404, detail="스킬을 찾을 수 없습니다")

    # Check if already installed
    existing = (
        db.query(SkillInstall)
        .filter(SkillInstall.skill_id == skill_id, SkillInstall.username == username, SkillInstall.uninstalled_at.is_(None))
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="이미 설치된 스킬입니다")

    install = SkillInstall(skill_id=skill_id, username=username)
    db.add(install)
    skill.install_count = (skill.install_count or 0) + 1
    db.commit()

    return {"installed": True, "skill_name": skill.skill_name, "dir_name": skill.skill_dir_name}


@router.post("/{skill_id}/uninstall")
async def uninstall_skill(
    skill_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """스킬 제거."""
    username = current_user["sub"]

    install = (
        db.query(SkillInstall)
        .filter(SkillInstall.skill_id == skill_id, SkillInstall.username == username, SkillInstall.uninstalled_at.is_(None))
        .first()
    )
    if not install:
        raise HTTPException(status_code=404, detail="설치된 스킬을 찾을 수 없습니다")

    install.uninstalled_at = datetime.now(timezone.utc)
    skill = db.query(SharedSkill).filter(SharedSkill.id == skill_id).first()
    if skill and skill.install_count > 0:
        skill.install_count -= 1
    db.commit()

    return {"uninstalled": True, "skill_id": skill_id}


@router.delete("/{skill_id}/unpublish")
async def unpublish_skill(
    skill_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """스킬 공유 해제."""
    username = current_user["sub"]

    skill = db.query(SharedSkill).filter(SharedSkill.id == skill_id, SharedSkill.owner_username == username).first()
    if not skill:
        raise HTTPException(status_code=404, detail="스킬을 찾을 수 없습니다")

    skill.is_active = False
    db.commit()
    return {"unpublished": True, "skill_name": skill.skill_name}
