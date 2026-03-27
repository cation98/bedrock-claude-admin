"""보안 정책 관리 API (관리자 전용).

Endpoints:
  GET    /api/v1/security/policies                   -- 전체 사용자 보안 정책 목록
  GET    /api/v1/security/policies/{user_id}         -- 단일 사용자 보안 정책 조회
  PUT    /api/v1/security/policies/{user_id}         -- 보안 정책 수정
  POST   /api/v1/security/templates/apply/{user_id}  -- 템플릿 적용
  GET    /api/v1/security/templates                  -- 템플릿 목록
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.security import (
    SECURITY_TEMPLATES,
    TEMPLATE_DESCRIPTIONS,
    ApplyTemplateRequest,
    SecurityPolicyData,
    SecurityPolicyUpdateRequest,
    SecurityTemplateItem,
    SecurityTemplateListResponse,
)
from app.services.audit_service import log_audit

router = APIRouter(prefix="/api/v1/security", tags=["security"])
logger = logging.getLogger(__name__)


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """관리자 권한 확인."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def _resolve_policy(user: User) -> dict:
    """사용자의 유효 보안 정책 반환 (None이면 standard 기본값)."""
    return user.security_policy if user.security_policy else SECURITY_TEMPLATES["standard"]


def _get_security_level(user: User) -> str:
    """사용자의 현재 보안 등급 문자열 반환."""
    policy = _resolve_policy(user)
    return policy.get("security_level", "standard")


# ==================== 정책 CRUD ====================


@router.get("/policies")
async def list_policies(
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """승인된 전체 사용자의 보안 정책 목록."""
    users = db.query(User).filter(User.is_approved == True).all()  # noqa: E712
    return {
        "total": len(users),
        "policies": [
            {
                "user_id": u.id,
                "username": u.username,
                "name": u.name,
                "region_name": u.region_name,
                "team_name": u.team_name,
                "role": u.role,
                "security_level": _get_security_level(u),
                "security_policy": _resolve_policy(u),
                "pod_restart_required": False,
            }
            for u in users
        ],
    }


@router.get("/policies/{user_id}")
async def get_policy(
    user_id: int,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """단일 사용자 보안 정책 조회."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "user_id": user.id,
        "username": user.username,
        "name": user.name,
        "security_level": _get_security_level(user),
        "security_policy": _resolve_policy(user),
    }


@router.put("/policies/{user_id}")
async def update_policy(
    user_id: int,
    req: SecurityPolicyUpdateRequest,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """사용자 보안 정책 직접 수정."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_level = _get_security_level(user)
    user.security_policy = req.security_policy.model_dump()
    db.commit()
    db.refresh(user)

    new_level = _get_security_level(user)
    log_audit(
        db,
        _admin["sub"],
        AuditAction.SECURITY_UPDATE,
        target=user.username,
        detail=f"{old_level}\u2192{new_level}",
    )
    db.commit()

    logger.info(f"Security policy updated for {user.username}: {old_level}\u2192{new_level}")
    return {
        "user_id": user.id,
        "username": user.username,
        "security_level": new_level,
        "security_policy": user.security_policy,
    }


# ==================== 템플릿 ====================


@router.post("/templates/apply/{user_id}")
async def apply_template(
    user_id: int,
    req: ApplyTemplateRequest,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """보안 템플릿을 사용자에게 적용."""
    if req.template_name not in SECURITY_TEMPLATES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid template: {req.template_name}. "
            f"Available: {', '.join(SECURITY_TEMPLATES.keys())}",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_level = _get_security_level(user)
    user.security_policy = SECURITY_TEMPLATES[req.template_name]
    db.commit()
    db.refresh(user)

    log_audit(
        db,
        _admin["sub"],
        AuditAction.SECURITY_TEMPLATE,
        target=user.username,
        detail=f"{old_level}\u2192{req.template_name}",
    )
    db.commit()

    logger.info(f"Template '{req.template_name}' applied to {user.username}")
    return {
        "user_id": user.id,
        "username": user.username,
        "security_level": req.template_name,
        "security_policy": user.security_policy,
    }


@router.get("/templates")
async def list_templates(
    _admin: dict = Depends(_require_admin),
):
    """사용 가능한 보안 템플릿 목록."""
    return SecurityTemplateListResponse(
        templates=[
            SecurityTemplateItem(
                name=name,
                description=TEMPLATE_DESCRIPTIONS.get(name, ""),
                security_policy=policy,
            )
            for name, policy in SECURITY_TEMPLATES.items()
        ]
    )
