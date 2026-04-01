"""스케줄링 + 연장 요청/승인 API.

Pod 수명은 사용자별 pod_ttl(7d/30d/unlimited) 기준으로만 관리.
업무시간(09-18) 기반 강제 종료는 폐지됨 (2026-04-01).

Endpoints:
  POST /api/v1/schedule/shutdown-warning  — [비활성] 종료 경고 (EventBridge DISABLED)
  POST /api/v1/schedule/shutdown          — [비활성] Pod 종료 (EventBridge DISABLED)
  POST /api/v1/schedule/startup           — [비활성] 노드 확장 (EventBridge DISABLED)
  POST /api/v1/schedule/extension/request — 연장 요청
  POST /api/v1/schedule/extension/approve/{id} — 연장 승인
  POST /api/v1/schedule/extension/reject/{id}  — 연장 거절
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/schedule", tags=["scheduling"])

# 관리자 사번 (텔레그램 알림 발송 대상)
ADMIN_USERNAME = "N1102359"


# ---------------------------------------------------------------------------
# DB Model
# ---------------------------------------------------------------------------

class ExtensionRequest(Base):
    """시간 연장 요청 테이블."""
    __tablename__ = "extension_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False)
    user_name = Column(String(100))
    requested_hours = Column(Integer, default=2)
    status = Column(String(20), default="pending")  # pending, approved, rejected
    requested_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True))
    resolved_by = Column(String(50))


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """관리자 권한 검증."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ===========================================================================
# Scheduling endpoints
# ===========================================================================

@router.post("/shutdown-warning")
async def send_shutdown_warning(
    minutes_before: int = 30,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """[비활성] 업무시간 종료 경고 — 24/7 운영 전환으로 사용하지 않음."""
    raise HTTPException(status_code=410, detail="업무시간 스케줄이 폐지되었습니다. Pod 수명은 사용자별 TTL로 관리됩니다.")
    # 아래 코드는 비활성 (EventBridge DISABLED, 2026-04-01)
    from app.models.session import TerminalSession

    sessions = db.query(TerminalSession).filter(
        TerminalSession.pod_status == "running"
    ).all()

    if not sessions:
        return {"warned": 0, "message": "실행 중인 세션이 없습니다"}

    warned = 0
    for sess in sessions:
        try:
            await _send_telegram_warning(sess.username, minutes_before, settings, db)
            warned += 1
        except Exception as e:
            logger.warning(f"Failed to warn {sess.username}: {e}")

    return {"warned": warned, "minutes_before": minutes_before}


@router.post("/shutdown")
async def execute_shutdown(
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """[비활성] 업무시간 종료 — 24/7 운영 전환으로 사용하지 않음."""
    raise HTTPException(status_code=410, detail="업무시간 스케줄이 폐지되었습니다. Pod 수명은 사용자별 TTL로 관리됩니다.")
    # 아래 코드는 비활성 (EventBridge DISABLED, 2026-04-01)
    from app.models.session import TerminalSession
    from app.services.k8s_service import K8sService

    k8s = K8sService(settings)
    sessions = db.query(TerminalSession).filter(
        TerminalSession.pod_status == "running"
    ).all()

    # 승인된 연장 중 아직 만료되지 않은 사용자 목록
    approved = set()
    pending_ext = db.query(ExtensionRequest).filter(
        ExtensionRequest.status == "approved",
        ExtensionRequest.resolved_at != None,  # noqa: E711
    ).all()
    for ext in pending_ext:
        if ext.resolved_at:
            expiry = ext.resolved_at + timedelta(hours=ext.requested_hours)
            if datetime.now(timezone.utc) < expiry:
                approved.add(ext.username)

    terminated = 0
    for sess in sessions:
        if sess.username in approved:
            logger.info(f"Skipping {sess.username} — extension approved")
            continue
        try:
            # 대화이력 EFS 백업 후 Pod 삭제 (실패해도 삭제 계속)
            from app.services.idle_cleanup_service import IdleCleanupService
            backup_svc = IdleCleanupService(k8s)
            backup_svc._backup_pod(sess.pod_name, k8s.namespace)

            k8s.delete_pod(sess.pod_name)
            sess.pod_status = "terminated"
            sess.terminated_at = datetime.now(timezone.utc)
            terminated += 1
            log_audit(db, "SCHEDULER", "pod_terminate", target=sess.pod_name,
                      detail="business hours shutdown")
        except Exception as e:
            logger.error(f"Failed to terminate {sess.pod_name}: {e}")

    db.commit()
    return {"terminated": terminated, "skipped_extended": len(approved)}


@router.post("/startup")
async def execute_startup(
    desired_nodes: int = 2,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """[비활성] 업무시간 시작 — 24/7 운영 전환으로 사용하지 않음."""
    raise HTTPException(status_code=410, detail="업무시간 스케줄이 폐지되었습니다. 노드는 자동 스케일링으로 관리됩니다.")
    # 아래 코드는 비활성 (EventBridge DISABLED, 2026-04-01)
    import boto3

    eks = boto3.client("eks", region_name="ap-northeast-2")
    cluster = "bedrock-claude-eks"

    try:
        ng = eks.describe_nodegroup(
            clusterName=cluster, nodegroupName="bedrock-claude-nodes",
        )["nodegroup"]
        current = ng["scalingConfig"]["desiredSize"]
        if current < desired_nodes:
            eks.update_nodegroup_config(
                clusterName=cluster,
                nodegroupName="bedrock-claude-nodes",
                scalingConfig={
                    "minSize": 0,
                    "maxSize": int(ng["scalingConfig"]["maxSize"]),
                    "desiredSize": desired_nodes,
                },
            )
            return {"nodegroup": "bedrock-claude-nodes", "scaled": f"{current} → {desired_nodes}"}
        return {"nodegroup": "bedrock-claude-nodes", "status": f"already at {current}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# Extension request list (admin)
# ===========================================================================

@router.get("/extensions")
async def list_extensions(
    status_filter: str = None,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """연장 요청 목록 조회."""
    query = db.query(ExtensionRequest).order_by(ExtensionRequest.requested_at.desc())
    if status_filter:
        query = query.filter(ExtensionRequest.status == status_filter)
    requests = query.limit(50).all()
    return {
        "requests": [
            {
                "id": r.id,
                "username": r.username,
                "user_name": r.user_name,
                "requested_hours": r.requested_hours,
                "status": r.status,
                "requested_at": r.requested_at.isoformat() if r.requested_at else None,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                "resolved_by": r.resolved_by,
            }
            for r in requests
        ]
    }


# ===========================================================================
# Extension request endpoints
# ===========================================================================

class ExtensionRequestBody(BaseModel):
    hours: int = 2


@router.post("/extension/request")
async def request_extension(
    body: ExtensionRequestBody,
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """사용자가 연장 요청 → 관리자에게 텔레그램 알림."""
    username = current_user["sub"]

    # 이미 대기 중인 요청이 있으면 중복 방지
    existing = db.query(ExtensionRequest).filter(
        ExtensionRequest.username == username,
        ExtensionRequest.status == "pending",
    ).first()
    if existing:
        return {"status": "already_pending", "message": "이미 연장 요청이 대기 중입니다"}

    # 사용자 이름 조회
    from app.models.user import User
    user = db.query(User).filter(User.username == username).first()
    user_name = user.name if user else username

    req = ExtensionRequest(
        username=username,
        user_name=user_name,
        requested_hours=body.hours,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    # 관리자에게 텔레그램 알림
    await _notify_admin_extension(username, user_name, body.hours, req.id, settings, db)

    log_audit(db, username, "extension_request", detail=f"{body.hours}시간 연장 요청")
    db.commit()

    return {
        "status": "requested",
        "request_id": req.id,
        "message": f"{body.hours}시간 연장을 요청했습니다. 관리자 승인을 기다려주세요.",
    }


@router.post("/extension/approve/{request_id}")
async def approve_extension(
    request_id: int,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """관리자가 연장 승인."""
    req = db.query(ExtensionRequest).filter(ExtensionRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        return {"status": req.status, "message": f"이미 {req.status} 상태입니다"}

    req.status = "approved"
    req.resolved_at = datetime.now(timezone.utc)
    req.resolved_by = _admin["sub"]
    db.commit()

    # 사용자에게 결과 알림
    await _notify_user_extension_result(req.username, "approved", req.requested_hours, settings, db)

    log_audit(db, _admin["sub"], "extension_approve", target=req.username,
              detail=f"{req.requested_hours}시간 연장 승인")
    db.commit()

    return {"status": "approved", "username": req.username, "hours": req.requested_hours}


@router.post("/extension/reject/{request_id}")
async def reject_extension(
    request_id: int,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """관리자가 연장 거절."""
    req = db.query(ExtensionRequest).filter(ExtensionRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    req.status = "rejected"
    req.resolved_at = datetime.now(timezone.utc)
    req.resolved_by = _admin["sub"]
    db.commit()

    await _notify_user_extension_result(req.username, "rejected", 0, settings, db)

    log_audit(db, _admin["sub"], "extension_reject", target=req.username)
    db.commit()

    return {"status": "rejected", "username": req.username}


# ===========================================================================
# Telegram helper functions
# ===========================================================================

def _get_telegram_id(username: str, db: Session) -> int | None:
    """telegram_mappings 테이블에서 사용자의 telegram_id 조회.

    Telegram private chat에서는 chat_id == telegram_id 이므로
    telegram_id를 그대로 sendMessage의 chat_id로 사용할 수 있다.
    """
    try:
        from sqlalchemy import text
        result = db.execute(
            text("SELECT telegram_id FROM telegram_mappings WHERE username = :u"),
            {"u": username},
        ).fetchone()
        return result[0] if result else None
    except Exception:
        return None


async def _send_telegram_warning(username: str, minutes: int, settings: Settings, db: Session):
    """사용자에게 텔레그램 종료 경고."""
    import httpx

    telegram_id = _get_telegram_id(username, db)
    if not telegram_id:
        return

    token = settings.telegram_bot_token
    if not token:
        return

    msg = (
        f"⚠ Claude Code 세션이 {minutes}분 후 종료됩니다.\n"
        f"작업을 저장하세요.\n\n"
        f"추가 시간이 필요하면 /연장요청 을 입력하세요."
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": telegram_id, "text": msg},
        )


async def _notify_admin_extension(
    username: str, user_name: str, hours: int, req_id: int,
    settings: Settings, db: Session,
):
    """관리자에게 연장 요청 알림."""
    import httpx

    admin_telegram_id = _get_telegram_id(ADMIN_USERNAME, db)
    if not admin_telegram_id:
        logger.warning(f"Admin {ADMIN_USERNAME} telegram_id not found — skipping notification")
        return

    token = settings.telegram_bot_token
    if not token:
        return

    msg = (
        f"🔔 연장 요청\n"
        f"{user_name}({username})이 {hours}시간 연장을 요청했습니다.\n\n"
        f"/승인 {req_id}  또는  /거절 {req_id}"
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": admin_telegram_id, "text": msg},
        )


async def _notify_user_extension_result(
    username: str, result: str, hours: int,
    settings: Settings, db: Session,
):
    """사용자에게 연장 결과 알림."""
    import httpx

    telegram_id = _get_telegram_id(username, db)
    if not telegram_id:
        return

    token = settings.telegram_bot_token
    if not token:
        return

    if result == "approved":
        msg = f"✅ 연장 승인됨 — {hours}시간 추가 사용 가능합니다."
    else:
        msg = f"❌ 연장 거절됨 — 세션이 곧 종료됩니다. 작업을 저장하세요."

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": telegram_id, "text": msg},
        )
