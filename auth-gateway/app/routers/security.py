"""보안 정책 관리 API (관리자 전용).

Endpoints:
  GET    /api/v1/security/policies                   -- 전체 사용자 보안 정책 목록
  GET    /api/v1/security/policies/{user_id}         -- 단일 사용자 보안 정책 조회
  PUT    /api/v1/security/policies/{user_id}         -- 보안 정책 수정
  POST   /api/v1/security/templates/apply/{user_id}  -- 템플릿 적용
  GET    /api/v1/security/templates                  -- 템플릿 목록 (built-in + custom)
  GET    /api/v1/security/custom-templates            -- custom 템플릿 목록
  POST   /api/v1/security/custom-templates            -- custom 템플릿 생성
  PUT    /api/v1/security/custom-templates/{id}       -- custom 템플릿 수정
  DELETE /api/v1/security/custom-templates/{id}       -- custom 템플릿 삭제
  GET    /api/v1/security/tables                     -- Safety/TANGO DB 테이블 목록
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.audit_log import AuditAction
from app.models.user import SecurityTemplate, User
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
    """보안 템플릿을 사용자에게 적용 (built-in + custom 지원)."""
    # Check built-in templates first
    if req.template_name in SECURITY_TEMPLATES:
        policy = SECURITY_TEMPLATES[req.template_name]
    else:
        # Fall back to custom templates from DB
        custom = db.query(SecurityTemplate).filter(SecurityTemplate.name == req.template_name).first()
        if not custom:
            raise HTTPException(
                status_code=400,
                detail=f"Template '{req.template_name}' not found in built-in or custom templates",
            )
        policy = custom.policy

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_level = _get_security_level(user)
    user.security_policy = policy
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
    db: Session = Depends(get_db),
):
    """사용 가능한 보안 템플릿 목록 (built-in + custom)."""
    # Built-in templates
    built_in = [
        SecurityTemplateItem(
            name=name,
            description=TEMPLATE_DESCRIPTIONS.get(name, ""),
            security_policy=policy,
        )
        for name, policy in SECURITY_TEMPLATES.items()
    ]

    # Custom templates from DB
    custom_rows = db.query(SecurityTemplate).order_by(SecurityTemplate.name).all()
    custom = [
        SecurityTemplateItem(
            name=t.name,
            description=t.description or "",
            security_policy=t.policy,
        )
        for t in custom_rows
    ]

    return SecurityTemplateListResponse(templates=built_in + custom)


# ==================== Custom 템플릿 CRUD ====================


@router.get("/custom-templates")
async def list_custom_templates(
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """모든 custom 보안 정책 템플릿 목록."""
    templates = db.query(SecurityTemplate).order_by(SecurityTemplate.name).all()
    return {
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "policy": t.policy,
                "created_by": t.created_by,
            }
            for t in templates
        ]
    }


@router.post("/custom-templates")
async def create_custom_template(
    req: dict,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """새 custom 보안 정책 템플릿 생성."""
    if not req.get("name"):
        raise HTTPException(status_code=400, detail="Template name is required")
    if not req.get("policy"):
        raise HTTPException(status_code=400, detail="Template policy is required")

    # Reject names that collide with built-in templates
    if req["name"] in SECURITY_TEMPLATES:
        raise HTTPException(
            status_code=400,
            detail=f"'{req['name']}' is a built-in template name and cannot be used",
        )

    existing = db.query(SecurityTemplate).filter(SecurityTemplate.name == req["name"]).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"'{req['name']}' 템플릿이 이미 존재합니다")

    template = SecurityTemplate(
        name=req["name"],
        description=req.get("description", ""),
        policy=req["policy"],
        created_by=_admin["sub"],
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    log_audit(
        db,
        _admin["sub"],
        AuditAction.SECURITY_TEMPLATE,
        target=req["name"],
        detail="custom template created",
    )
    db.commit()

    logger.info(f"Custom template '{template.name}' created by {_admin['sub']}")
    return {"id": template.id, "name": template.name, "policy": template.policy}


@router.put("/custom-templates/{template_id}")
async def update_custom_template(
    template_id: int,
    req: dict,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """custom 템플릿 수정."""
    template = db.query(SecurityTemplate).filter(SecurityTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # If renaming, reject built-in name collisions
    if "name" in req and req["name"] in SECURITY_TEMPLATES:
        raise HTTPException(
            status_code=400,
            detail=f"'{req['name']}' is a built-in template name and cannot be used",
        )

    if "name" in req:
        # Check uniqueness against other custom templates
        conflict = (
            db.query(SecurityTemplate)
            .filter(SecurityTemplate.name == req["name"], SecurityTemplate.id != template_id)
            .first()
        )
        if conflict:
            raise HTTPException(status_code=400, detail=f"'{req['name']}' 템플릿이 이미 존재합니다")
        template.name = req["name"]

    if "description" in req:
        template.description = req["description"]
    if "policy" in req:
        template.policy = req["policy"]

    db.commit()
    db.refresh(template)

    log_audit(
        db,
        _admin["sub"],
        AuditAction.SECURITY_TEMPLATE,
        target=template.name,
        detail="custom template updated",
    )
    db.commit()

    logger.info(f"Custom template '{template.name}' updated by {_admin['sub']}")
    return {"id": template.id, "name": template.name, "policy": template.policy}


@router.delete("/custom-templates/{template_id}")
async def delete_custom_template(
    template_id: int,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """custom 템플릿 삭제."""
    template = db.query(SecurityTemplate).filter(SecurityTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    name = template.name
    db.delete(template)
    db.commit()

    log_audit(
        db,
        _admin["sub"],
        AuditAction.SECURITY_TEMPLATE,
        target=name,
        detail="custom template deleted",
    )
    db.commit()

    logger.info(f"Custom template '{name}' deleted by {_admin['sub']}")
    return {"deleted": True, "name": name}


# ==================== 테이블 목록 ====================

# Table descriptions for each DB (Korean)
SAFETY_TABLE_DESCRIPTIONS = {
    "safety_activity_tbmactivity": "TBM 활동 기록",
    "safety_activity_tbmactivity_companion": "TBM 동행자",
    "safety_activity_tbmactivityimages": "TBM 사진",
    "safety_activity_workinfo": "작업 정보 (region, team)",
    "safety_activity_workstatus": "작업 상태",
    "safety_activity_workstatushistory": "작업 상태 이력",
    "safety_activity_worktype": "작업 유형",
    "safety_activity_workstophistory": "작업 중지 이력",
    "safety_activity_workstophistoryimages": "작업 중지 사진",
    "safety_activity_patrolsafetyinspection": "순찰 안전점검",
    "safety_activity_patrolsafetyinspectchecklist": "점검 체크리스트",
    "safety_activity_patrolsafetyinspectiongoodandbad": "양호/불량 판정",
    "safety_activity_patrolsafetyjointinspection": "합동 점검",
    "safety_activity_weeklyworkplanfrombp": "BP별 주간 작업계획",
    "safety_activity_weeklyworkplanperskoregion": "SKO 담당별 주간계획",
    "safety_activity_weeklyworkplanperskoteam": "SKO 팀별 주간계획",
    "she_measurement_sherecord": "SHE 측정 기록",
    "she_measurement_shecategory": "SHE 카테고리",
    "she_measurement_sheitemscore": "SHE 항목 점수",
    "compliance_check_checklistrecord": "컴플라이언스 점검 기록",
    "compliance_check_checklistitem": "점검 항목",
    "committee_workriskassessment": "작업 위험성 평가",
    "committee_maincommitteelist": "안전위원회 목록",
    "board_post": "게시글",
    "board_comment": "댓글",
    "board_file": "첨부파일",
    "sysmanage_region": "담당 조직",
    "sysmanage_teamregion": "팀 조직",
    "sysmanage_companymaster": "협력사 마스터",
}

TANGO_TABLE_DESCRIPTIONS = {
    "alarm_data": "활성 알람 (실시간)",
    "alarm_events": "30일 분석용 이벤트",
    "alarm_history": "복구된 알람 이력",
    "alarm_hourly_summary": "시간별 집계 통계",
    "alarm_statistics": "팀별 통계 뷰",
    "alarm_raw_logs": "원본 알람 레코드",
    "facility_info": "장비 참조 테이블",
    "opark_daily_report": "OPAC 업무일지 (47컬럼)",
    "report_embeddings": "벡터 임베딩 (ko-sroberta)",
    "report_ontology": "5단계 업무 분류 트리",
    "report_alarm_matches": "알람-업무 유사도 매칭",
    "opark_equipmaster": "장비 마스터",
    "opark_b2bequipmaster": "B2B 장비 마스터",
    "opark_cmsequipmaster": "CMS 장비 마스터",
    "opark_evchrgequipmaster": "전기차 충전 장비",
    "opark_fronthaulequipmaster": "Fronthaul 장비",
}

DOCULOG_TABLE_DESCRIPTIONS = {
    "document_logs": "문서활동 로그 원본 + 분석 컬럼 (4.6M rows)",
    "task_embeddings": "업무명 임베딩 벡터 (768dim, 360K)",
    "mv_pre_reorg": "2025년 개편 전 데이터 뷰",
}

# Blacklisted tables (never shown, never accessible)
BLACKLISTED_TABLES = {
    "auth_user", "accounts_userprofile", "accounts_passwordhistory",
    "accounts_activesession", "accounts_smsverification",
    "accounts_phonebasedsmsverifyattempts", "accounts_userbasedsmsverifyattempt",
    "accounts_userconsent", "accounts_consentagreement",
    "accounts_loginauditlog", "accounts_dormantuserconfig",
    "accounts_passwordexpiryconfig", "accounts_userregisterphonechecker",
    "accounts_userroles", "accounts_userroleshistory",
    "accounts_accountdeletionrequest", "accounts_companychangerequest",
    "accounts_adminnotification", "accounts_userprofile_extra_role",
    "django_session", "django_admin_log", "django_migrations",
    "django_content_type", "django_celery_beat_periodictask",
    "django_celery_beat_periodictasks", "django_celery_beat_crontabschedule",
    "django_celery_beat_intervalschedule", "django_celery_beat_clockedschedule",
    "django_celery_beat_solarschedule", "django_celery_results_taskresult",
    "django_celery_results_groupresult",
    "token_blacklist_blacklistedtoken", "token_blacklist_outstandingtoken",
    "auth_group", "auth_group_permissions", "auth_permission",
    "auth_user_groups", "auth_user_user_permissions",
}


@router.get("/tables")
async def list_tables(
    _admin=Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """Safety/TANGO/Docu-Log DB의 테이블 목록 조회 (시스템 테이블 제외, 설명 포함)."""
    import psycopg2

    result = {"safety": [], "tango": [], "doculog": []}

    # Safety DB
    try:
        conn = psycopg2.connect(settings.workshop_database_url)
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        for (table_name,) in cur.fetchall():
            if table_name not in BLACKLISTED_TABLES and not table_name.startswith("django_"):
                result["safety"].append({
                    "name": table_name,
                    "description": SAFETY_TABLE_DESCRIPTIONS.get(table_name, ""),
                })
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to list safety tables: {e}")

    # TANGO DB
    try:
        tango_url = settings.tango_database_url
        if tango_url:
            conn = psycopg2.connect(tango_url)
            cur = conn.cursor()
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            for (table_name,) in cur.fetchall():
                if table_name not in BLACKLISTED_TABLES:
                    result["tango"].append({
                        "name": table_name,
                        "description": TANGO_TABLE_DESCRIPTIONS.get(table_name, ""),
                    })
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to list tango tables: {e}")

    # Docu-Log DB
    try:
        doculog_url = settings.doculog_database_url
        if doculog_url:
            conn = psycopg2.connect(doculog_url)
            cur = conn.cursor()
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            for (table_name,) in cur.fetchall():
                result["doculog"].append({
                    "name": table_name,
                    "description": DOCULOG_TABLE_DESCRIPTIONS.get(table_name, ""),
                })
            # Also include materialized views
            cur.execute("SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'")
            for (mv,) in cur.fetchall():
                result["doculog"].append({
                    "name": mv,
                    "description": DOCULOG_TABLE_DESCRIPTIONS.get(mv, "(materialized view)"),
                })
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to list doculog tables: {e}")

    return result
