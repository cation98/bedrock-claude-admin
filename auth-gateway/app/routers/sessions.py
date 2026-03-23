"""세션(터미널 Pod) 관리 API 라우터.

Endpoints:
  POST   /api/v1/sessions/         — 내 터미널 세션 시작 (Pod 생성)
  GET    /api/v1/sessions/         — 내 세션 목록
  GET    /api/v1/sessions/my-terminal — 내 활성 터미널 Pod IP 조회
  DELETE /api/v1/sessions/{id}     — 내 세션 종료 (Pod 삭제)
  GET    /api/v1/sessions/active   — 모든 활성 세션 (관리자)
  POST   /api/v1/sessions/bulk     — 일괄 세션 생성 (관리자)
  DELETE /api/v1/sessions/bulk     — 일괄 세션 종료 (관리자)
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.session import TerminalSession
from app.schemas.session import (
    BulkSessionRequest,
    SessionCreateRequest,
    SessionListResponse,
    SessionResponse,
)
from app.schemas.user import POD_TTL_SECONDS_MAP
from app.services.k8s_service import K8sService, K8sServiceError

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)


def _get_k8s_service(settings: Settings = Depends(get_settings)) -> K8sService:
    return K8sService(settings)


def _to_response(session: TerminalSession, settings: Settings, ttl_seconds: int | None = None) -> SessionResponse:
    """DB 세션 → API 응답 변환.

    Args:
        session: DB 세션 레코드
        settings: 앱 설정
        ttl_seconds: Pod TTL(초). 0이면 unlimited(만료 없음), None이면 기본값 사용.
    """
    terminal_url = None
    if session.pod_status == "running" and session.pod_name:
        # 실제 환경에서는 Ingress URL로 교체
        terminal_url = f"/terminal/{session.pod_name}/"

    # expires_at 계산: ttl_seconds > 0 이고 started_at이 있으면 만료 시간 산출
    expires_at = None
    if ttl_seconds and ttl_seconds > 0 and session.started_at:
        expires_at = session.started_at + timedelta(seconds=ttl_seconds)

    return SessionResponse(
        id=session.id,
        username=session.username,
        pod_name=session.pod_name or "",
        pod_status=session.pod_status,
        session_type=session.session_type,
        terminal_url=terminal_url,
        started_at=session.started_at,
        terminated_at=session.terminated_at,
        expires_at=expires_at,
    )


# ==================== 사용자 API ====================


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: SessionCreateRequest,
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    k8s: K8sService = Depends(_get_k8s_service),
    db: Session = Depends(get_db),
):
    """내 터미널 세션 시작 (Pod 생성)."""
    username = current_user["sub"]

    # 이미 활성 세션이 있는지 확인
    existing = (
        db.query(TerminalSession)
        .filter(
            TerminalSession.username == username,
            TerminalSession.pod_status.in_(["creating", "running"]),
        )
        .first()
    )
    if existing:
        return _to_response(existing, settings)

    # 사용자 표시 이름 + Pod TTL 조회
    from app.models.user import User
    user = db.query(User).filter(User.username == username).first()
    user_display_name = user.name if user and user.name else username

    # 사용자별 Pod TTL 결정 (DB 설정 → 초 변환)
    user_pod_ttl = user.pod_ttl if user else "4h"
    ttl_seconds = POD_TTL_SECONDS_MAP.get(user_pod_ttl, 14400)

    # K8s Pod 생성 (사용자 프로필 주입 + 동적 TTL)
    try:
        pod_name = k8s.create_pod(username, request.session_type, user_display_name, ttl_seconds=ttl_seconds)
    except K8sServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # DB에 세션 기록
    session = TerminalSession(
        user_id=current_user["user_id"],
        username=username,
        pod_name=pod_name,
        pod_status="creating",
        session_type=request.session_type,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # Pod 상태 확인 후 업데이트
    pod_status = k8s.get_pod_status(pod_name)
    if pod_status and pod_status["phase"] == "Running":
        session.pod_status = "running"
        db.commit()

    return _to_response(session, settings, ttl_seconds=ttl_seconds)


@router.get("/", response_model=SessionListResponse)
async def list_my_sessions(
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    k8s: K8sService = Depends(_get_k8s_service),
    db: Session = Depends(get_db),
):
    """내 세션 목록 (K8s 상태 동기화 포함)."""
    sessions = (
        db.query(TerminalSession)
        .filter(TerminalSession.username == current_user["sub"])
        .order_by(TerminalSession.created_at.desc())
        .limit(20)
        .all()
    )

    # creating 상태인 세션의 Pod 상태를 K8s에서 동기화
    for session in sessions:
        if session.pod_status == "creating" and session.pod_name:
            pod_status = k8s.get_pod_status(session.pod_name)
            if pod_status and pod_status["phase"] == "Running":
                session.pod_status = "running"
            elif pod_status and pod_status["phase"] in ("Failed", "Succeeded"):
                session.pod_status = "terminated"
    db.commit()

    return SessionListResponse(
        total=len(sessions),
        sessions=[_to_response(s, settings) for s in sessions],
    )


# ==================== 관리자 API (/{session_id} 보다 먼저 선언) ====================


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """관리자 권한 확인."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/active", response_model=SessionListResponse)
async def list_active_sessions(
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    k8s: K8sService = Depends(_get_k8s_service),
    db: Session = Depends(get_db),
):
    """모든 활성 세션 목록 (관리자용)."""
    sessions = (
        db.query(TerminalSession)
        .filter(TerminalSession.pod_status.in_(["creating", "running"]))
        .all()
    )

    # K8s에서 실제 Pod 상태 동기화
    for session in sessions:
        if session.pod_name:
            pod_status = k8s.get_pod_status(session.pod_name)
            if pod_status is None:
                session.pod_status = "terminated"
                session.terminated_at = datetime.now(timezone.utc)
            elif pod_status["phase"] == "Running":
                session.pod_status = "running"
            elif pod_status["phase"] in ("Failed", "Succeeded"):
                session.pod_status = "terminated"
                session.terminated_at = datetime.now(timezone.utc)
    db.commit()

    active = [s for s in sessions if s.pod_status in ("creating", "running")]
    return SessionListResponse(
        total=len(active),
        sessions=[_to_response(s, settings) for s in active],
    )


@router.post("/bulk", response_model=SessionListResponse)
async def bulk_create_sessions(
    request: BulkSessionRequest,
    admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    k8s: K8sService = Depends(_get_k8s_service),
    db: Session = Depends(get_db),
):
    """일괄 세션 생성 (관리자용 — 실습 시작 시 사용)."""
    created_sessions = []

    for username in request.usernames:
        try:
            pod_name = k8s.create_pod(username, request.session_type)
            session = TerminalSession(
                user_id=0,  # bulk에서는 user_id 없이 생성
                username=username,
                pod_name=pod_name,
                pod_status="creating",
                session_type=request.session_type,
            )
            db.add(session)
            created_sessions.append(session)
        except K8sServiceError as e:
            logger.error(f"Failed to create session for {username}: {e}")

    db.commit()
    for s in created_sessions:
        db.refresh(s)

    return SessionListResponse(
        total=len(created_sessions),
        sessions=[_to_response(s, settings) for s in created_sessions],
    )


@router.delete("/bulk")
async def bulk_terminate_sessions(
    _admin: dict = Depends(_require_admin),
    k8s: K8sService = Depends(_get_k8s_service),
    db: Session = Depends(get_db),
):
    """모든 활성 세션 일괄 종료 (관리자용 — 실습 종료 시 사용)."""
    deleted_count = k8s.delete_all_pods()

    # DB 업데이트
    active_sessions = (
        db.query(TerminalSession)
        .filter(TerminalSession.pod_status.in_(["creating", "running"]))
        .all()
    )
    for session in active_sessions:
        session.pod_status = "terminated"
        session.terminated_at = datetime.now(timezone.utc)
    db.commit()

    return {"terminated": deleted_count, "message": f"{deleted_count} sessions terminated"}


@router.delete("/admin/{session_id}")
async def admin_terminate_session(
    session_id: int,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    k8s: K8sService = Depends(_get_k8s_service),
    db: Session = Depends(get_db),
):
    """관리자용 개별 세션 종료."""
    session = db.query(TerminalSession).filter(TerminalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.pod_name and session.pod_status != "terminated":
        try:
            k8s.delete_pod(session.pod_name)
        except K8sServiceError as e:
            logger.error(f"Failed to delete pod: {e}")

    session.pod_status = "terminated"
    session.terminated_at = datetime.now(timezone.utc)
    db.commit()

    return _to_response(session, settings)


# ==================== 사용자 API (동적 경로는 마지막에) ====================


@router.delete("/{session_id}", response_model=SessionResponse)
async def terminate_session(
    session_id: int,
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    k8s: K8sService = Depends(_get_k8s_service),
    db: Session = Depends(get_db),
):
    """내 세션 종료 (Pod 삭제)."""
    session = (
        db.query(TerminalSession)
        .filter(
            TerminalSession.id == session_id,
            TerminalSession.username == current_user["sub"],
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.pod_name and session.pod_status != "terminated":
        try:
            k8s.delete_pod(session.pod_name)
        except K8sServiceError as e:
            logger.error(f"Failed to delete pod: {e}")

    session.pod_status = "terminated"
    session.terminated_at = datetime.now(timezone.utc)
    db.commit()

    return _to_response(session, settings)
