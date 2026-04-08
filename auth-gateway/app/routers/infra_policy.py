"""인프라 정책 관리 API (관리자 전용)."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.infra_policy import InfraTemplate, INFRA_TEMPLATES, INFRA_TEMPLATE_DESCRIPTIONS
from app.models.user import User
from app.services.audit_service import log_audit
from app.models.audit_log import AuditAction

router = APIRouter(prefix="/api/v1/infra-policy", tags=["infra-policy"])
logger = logging.getLogger(__name__)


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/templates")
async def list_templates(_admin=Depends(_require_admin), db: Session = Depends(get_db)):
    """전체 인프라 정책 템플릿 (built-in + custom)."""
    custom_map = {t.name: t for t in db.query(InfraTemplate).order_by(InfraTemplate.name).all()}

    templates = []
    # Built-in (skip if custom override exists)
    for name, policy in INFRA_TEMPLATES.items():
        if name not in custom_map:
            templates.append({
                "name": name,
                "description": INFRA_TEMPLATE_DESCRIPTIONS.get(name, ""),
                "policy": policy,
                "is_builtin": True,
            })
    # Custom
    for t in custom_map.values():
        templates.append({
            "id": t.id,
            "name": t.name,
            "description": t.description or "",
            "policy": t.policy,
            "is_builtin": False,
        })

    return {"templates": templates}


@router.post("/templates")
async def create_template(req: dict, _admin=Depends(_require_admin), db: Session = Depends(get_db)):
    """custom 인프라 정책 생성."""
    existing = db.query(InfraTemplate).filter(InfraTemplate.name == req["name"]).first()
    if existing:
        # Upsert
        existing.description = req.get("description", existing.description)
        existing.policy = req["policy"]
        existing.created_by = _admin["sub"]
        db.commit()
        db.refresh(existing)
        return {"id": existing.id, "name": existing.name, "policy": existing.policy}

    template = InfraTemplate(
        name=req["name"],
        description=req.get("description", ""),
        policy=req["policy"],
        created_by=_admin["sub"],
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    log_audit(db, _admin["sub"], "infra_template_create", target=req["name"])
    db.commit()
    return {"id": template.id, "name": template.name, "policy": template.policy}


@router.put("/templates/{template_id}")
async def update_template(template_id: int, req: dict, _admin=Depends(_require_admin), db: Session = Depends(get_db)):
    t = db.query(InfraTemplate).filter(InfraTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    if "name" in req: t.name = req["name"]
    if "description" in req: t.description = req["description"]
    if "policy" in req: t.policy = req["policy"]
    db.commit()
    return {"id": t.id, "name": t.name, "policy": t.policy}


@router.delete("/templates/{template_id}")
async def delete_template(template_id: int, _admin=Depends(_require_admin), db: Session = Depends(get_db)):
    t = db.query(InfraTemplate).filter(InfraTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    name = t.name
    db.delete(t)
    db.commit()
    return {"deleted": True, "name": name}


@router.get("/assignments")
async def list_assignments(_admin=Depends(_require_admin), db: Session = Depends(get_db)):
    """사용자별 인프라 정책 할당 현황."""
    users = db.query(User).filter(User.is_approved == True).all()
    return {
        "assignments": [{
            "user_id": u.id,
            "username": u.username,
            "name": u.name,
            "infra_policy_name": (u.infra_policy or {}).get("_template_name", "standard"),
            "infra_policy": u.infra_policy or INFRA_TEMPLATES["standard"],
        } for u in users]
    }


@router.post("/assign")
async def assign_policy(req: dict, _admin=Depends(_require_admin), db: Session = Depends(get_db)):
    """사용자에게 인프라 정책 할당 (개별/일괄)."""
    template_name = req.get("template_name", "standard")
    usernames = req.get("usernames", [])

    # Resolve template
    if template_name in INFRA_TEMPLATES:
        policy = INFRA_TEMPLATES[template_name]
    else:
        custom = db.query(InfraTemplate).filter(InfraTemplate.name == template_name).first()
        if not custom:
            raise HTTPException(status_code=400, detail=f"Template '{template_name}' not found")
        policy = custom.policy

    # Add template name for reference
    policy_with_name = {**policy, "_template_name": template_name}

    assigned = 0
    for username in usernames:
        user = db.query(User).filter(User.username == username.upper()).first()
        if user:
            user.infra_policy = policy_with_name
            assigned += 1

    db.commit()

    log_audit(db, _admin["sub"], "infra_policy_assign",
              target=",".join(usernames), detail=f"template={template_name}")
    db.commit()

    return {"assigned": assigned, "template": template_name}
