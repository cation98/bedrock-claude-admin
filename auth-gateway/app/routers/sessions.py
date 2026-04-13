"""세션(터미널 Pod) 관리 API 라우터.

Endpoints:
  POST   /api/v1/sessions/         — 내 터미널 세션 시작 (Pod 생성)
  GET    /api/v1/sessions/         — 내 세션 목록
  GET    /api/v1/sessions/my-terminal — 내 활성 터미널 Pod IP 조회
  DELETE /api/v1/sessions/{id}     — 내 세션 종료 (Pod 삭제)
  GET    /api/v1/sessions/active   — 모든 활성 세션 (관리자)
  POST   /api/v1/sessions/bulk     — 일괄 세션 생성 (관리자)
  DELETE /api/v1/sessions/bulk     — 일괄 세션 종료 (관리자)
  POST   /api/v1/sessions/ui-source — UI 소스 사용 이벤트 기록 (T23)
"""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

import boto3
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from kubernetes import client as k8s_client
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
    UiSourceRequest,
)
from app.schemas.ui_split import UiSplitBucket, UiSplitSummary
from app.schemas.user import POD_TTL_SECONDS_MAP
from app.services.k8s_service import K8sService, K8sServiceError

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)

# ---------- 노드 용량 확인 및 오토스케일링 ----------

# EKS 클러스터 정보
EKS_CLUSTER_NAME = "bedrock-claude-eks"
EKS_REGION = "ap-northeast-2"

# 노드그룹 매핑: 템플릿별 노드그룹 참조
NODEGROUP_MAP = {
    "standard": "bedrock-claude-dedicated-nodes",
    "premium": "bedrock-claude-nodes",
    "enterprise": "presenter-node",
}

# Pod 생성에 필요한 최소 여유 CPU (millicores)
MIN_FREE_CPU_MILLICORES = 1000


def _parse_cpu_to_millicores(cpu_str: str) -> int:
    """K8s CPU 문자열을 millicore 정수로 변환.

    Examples:
        "4" → 4000, "3500m" → 3500, "750m" → 750
    """
    cpu_str = cpu_str.strip()
    if cpu_str.endswith("m"):
        return int(cpu_str[:-1])
    return int(float(cpu_str) * 1000)


def _parse_memory_to_bytes(mem_str: str) -> int:
    """K8s 메모리 문자열을 바이트 정수로 변환 (Ki/Mi/Gi 지원)."""
    mem_str = mem_str.strip()
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}
    for suffix, multiplier in units.items():
        if mem_str.endswith(suffix):
            return int(mem_str[: -len(suffix)]) * multiplier
    # 단위 없는 순수 바이트
    return int(mem_str)


def _ensure_node_capacity(username: str, security_policy: dict | None = None, infra_policy: dict | None = None) -> None:
    """Pod 생성 전 대상 노드그룹에 충분한 CPU 여유가 있는지 확인.

    여유가 부족하면 해당 노드그룹의 desiredSize를 +1 스케일업한다.
    노드가 실제로 Ready 될 때까지 기다리지 않는다 — Pod는 Pending 상태로
    대기하다가 노드가 준비되면 자동 스케줄링된다.

    IRSA(platform-admin-sa)의 eks:UpdateNodegroupConfig 권한을 사용한다.

    Args:
        username: 사번
        security_policy: DB 보안 정책 (DB 자격증명 제어용).
        infra_policy: 인프라 정책. 노드그룹, 노드 셀렉터, Pod 수 제한 등.
    """
    from app.models.infra_policy import INFRA_TEMPLATES as INFRA_DEFAULTS
    infra = infra_policy or INFRA_DEFAULTS["standard"]
    target_nodegroup = infra.get("nodegroup", "bedrock-claude-nodes")
    node_label = infra.get("node_selector")  # e.g., {"role": "presenter"} or None
    max_pods = infra.get("max_pods_per_node", 3)

    try:
        # K8s API로 해당 라벨의 노드 목록 조회
        v1 = k8s_client.CoreV1Api()

        if node_label:
            label_str = ",".join(f"{k}={v}" for k, v in node_label.items())
            nodes = v1.list_node(label_selector=label_str).items
        else:
            # 일반 사용자: claude-terminal 또는 claude-dedicated 노드
            all_nodes = v1.list_node().items
            nodes = [
                n for n in all_nodes
                if n.metadata.labels.get("role") in ("claude-terminal", "claude-dedicated")
            ]

        # cordon된 노드가 있으면 uncordon (이전 auto-scale-down 잔여)
        cordoned = [n for n in nodes if n.spec.unschedulable]
        for n in cordoned:
            try:
                v1.patch_node(n.metadata.name, {"spec": {"unschedulable": None}})
                logger.info(f"Uncordoned node {n.metadata.name} for new pod scheduling")
            except Exception as e:
                logger.warning(f"Failed to uncordon {n.metadata.name}: {e}")

        # cordon된 노드를 제외하지 않고 포함 (uncordon 완료)
        schedulable_nodes = [n for n in nodes if not n.spec.unschedulable]

        if not schedulable_nodes and not cordoned:
            # 노드가 0개이고 uncordon할 것도 없음 — 스케일업 필요
            logger.warning(
                f"No nodes found for nodegroup '{target_nodegroup}'. Scaling up by 1."
            )
            _scale_up_nodegroup(target_nodegroup)
            return

        if not schedulable_nodes and cordoned:
            # uncordon만 했으므로 스케줄링 가능해짐
            logger.info(f"Uncordoned {len(cordoned)} nodes, skipping scale-up")
            return

        # 각 노드의 allocatable CPU에서 실행 중인 Pod의 CPU request 합계를 빼서 여유 계산
        for node in (schedulable_nodes or nodes):
            node_name = node.metadata.name
            allocatable_cpu = _parse_cpu_to_millicores(
                node.status.allocatable.get("cpu", "0")
            )

            # 해당 노드에서 실행 중인 Pod들의 CPU request 합산
            pods = v1.list_pod_for_all_namespaces(
                field_selector=f"spec.nodeName={node_name},status.phase!=Succeeded,status.phase!=Failed"
            ).items

            used_cpu = 0
            for pod in pods:
                for container in pod.spec.containers:
                    if container.resources and container.resources.requests:
                        cpu_req = container.resources.requests.get("cpu", "0")
                        used_cpu += _parse_cpu_to_millicores(cpu_req)
                # init container는 병렬 실행되지 않으므로 무시

            free_cpu = allocatable_cpu - used_cpu
            logger.info(
                f"Node {node_name}: allocatable={allocatable_cpu}m, "
                f"used={used_cpu}m, free={free_cpu}m"
            )

            # 노드당 사용자 Pod(app=claude-terminal) 수 제한 확인
            user_pod_count = len([
                p for p in pods
                if p.metadata.labels and p.metadata.labels.get("app") == "claude-terminal"
            ])
            if user_pod_count >= max_pods:
                logger.info(
                    f"Node {node_name} has {user_pod_count} user pods "
                    f"(max {max_pods}). Skipping."
                )
                continue  # 이 노드는 Pod 수 제한 초과 — 다음 노드 시도

            if free_cpu >= MIN_FREE_CPU_MILLICORES:
                # 충분한 여유가 있는 노드 발견 — 스케일업 불필요
                logger.info(
                    f"Node {node_name} has {free_cpu}m free CPU. No scale-up needed."
                )
                return

        # 모든 노드가 부족 — 스케일업
        logger.warning(
            f"All nodes in nodegroup '{target_nodegroup}' lack capacity. Scaling up by 1."
        )
        _scale_up_nodegroup(target_nodegroup)

    except Exception as e:
        # 용량 확인/스케일업 실패는 Pod 생성을 막지 않는다.
        # Pod가 Pending 상태로 남게 되지만, 최소한 세션 생성 자체는 진행.
        logger.error(f"Failed to ensure node capacity: {e}", exc_info=True)


def _scale_up_nodegroup(nodegroup_name: str) -> None:
    """EKS 노드그룹의 desiredSize를 현재값 + 1로 스케일업.

    boto3 EKS API를 사용하며, IRSA 자격증명이 자동으로 적용된다.
    """
    try:
        eks = boto3.client("eks", region_name=EKS_REGION)

        # 현재 노드그룹 상태 조회
        ng = eks.describe_nodegroup(
            clusterName=EKS_CLUSTER_NAME,
            nodegroupName=nodegroup_name,
        )
        scaling = ng["nodegroup"]["scalingConfig"]
        current_desired = scaling["desiredSize"]
        max_size = scaling["maxSize"]
        new_desired = current_desired + 1

        if new_desired > max_size:
            logger.error(
                f"Cannot scale up '{nodegroup_name}': desired {new_desired} "
                f"exceeds maxSize {max_size}. Increase maxSize in Terraform first."
            )
            return

        eks.update_nodegroup_config(
            clusterName=EKS_CLUSTER_NAME,
            nodegroupName=nodegroup_name,
            scalingConfig={
                "minSize": scaling["minSize"],
                "maxSize": max_size,
                "desiredSize": new_desired,
            },
        )
        logger.info(
            f"Scaled up nodegroup '{nodegroup_name}': "
            f"{current_desired} → {new_desired} (max={max_size})"
        )

    except Exception as e:
        logger.error(f"Failed to scale up nodegroup '{nodegroup_name}': {e}", exc_info=True)


def _get_k8s_service(settings: Settings = Depends(get_settings)) -> K8sService:
    return K8sService(settings)


def _to_response(session: TerminalSession, settings: Settings, ttl_seconds: int | None = None, user_name: str | None = None) -> SessionResponse:
    """DB 세션 → API 응답 변환.

    Args:
        session: DB 세션 레코드
        settings: 앱 설정
        ttl_seconds: Pod TTL(초). 0이면 unlimited(만료 없음), None이면 기본값 사용.
    """
    terminal_url = None
    files_url = None
    hub_url = None
    if session.pod_status == "running" and session.pod_name:
        terminal_url = f"/terminal/{session.pod_name}/"
        files_url = f"/files/{session.pod_name}/"
        hub_url = f"/hub/{session.pod_name}/"

    # expires_at 계산: ttl_seconds > 0 이고 started_at이 있으면 만료 시간 산출
    expires_at = None
    if ttl_seconds and ttl_seconds > 0 and session.started_at:
        expires_at = session.started_at + timedelta(seconds=ttl_seconds)

    # idle_minutes 계산: running 상태일 때만
    idle_minutes = None
    if session.pod_status == "running" and session.last_active_at:
        delta = datetime.now(timezone.utc) - session.last_active_at
        idle_minutes = int(delta.total_seconds() // 60)

    return SessionResponse(
        id=session.id,
        username=session.username,
        user_name=user_name,
        pod_name=session.pod_name or "",
        pod_status=session.pod_status,
        session_type=session.session_type,
        terminal_url=terminal_url,
        files_url=files_url,
        hub_url=hub_url,
        started_at=session.started_at,
        terminated_at=session.terminated_at,
        expires_at=expires_at,
        last_active_at=session.last_active_at,
        idle_minutes=idle_minutes,
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
        # K8s 실제 상태와 동기화 후 반환
        if existing.pod_status == "creating" and existing.pod_name:
            pod_info = k8s.get_pod_status(existing.pod_name)
            if pod_info and pod_info["phase"] == "Running":
                existing.pod_status = "running"
                db.commit()
            elif pod_info is None:
                # Pod이 사라진 경우 — terminated 처리 후 새로 생성
                existing.pod_status = "terminated"
                existing.terminated_at = datetime.now(timezone.utc)
                db.commit()
                # 아래 코드에서 새 Pod 생성으로 진행
                existing = None
        if existing:
            return _to_response(existing, settings)

    # 사용자 표시 이름 + Pod TTL 조회
    from app.models.user import User
    user = db.query(User).filter(User.username == username).first()
    user_display_name = user.name if user and user.name else username

    # 토큰 할당 정책 확인 — 한도 초과 시 세션 생성 차단
    from app.routers.admin import _check_user_quota
    quota_info = _check_user_quota(db, username)
    if quota_info and quota_info["is_exceeded"] and not quota_info["is_unlimited"]:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Token quota exceeded. "
                f"Limit: ${quota_info['cost_limit_usd']:.2f}/{quota_info['refresh_cycle']}, "
                f"Used: ${quota_info['current_usage_usd']:.4f}, "
                f"Cycle: {quota_info['cycle_start']} ~ {quota_info['cycle_end']}"
            ),
        )

    # 사용자별 Pod TTL 결정 (DB 설정 → 초 변환)
    user_pod_ttl = user.pod_ttl if user else "4h"
    ttl_seconds = POD_TTL_SECONDS_MAP.get(user_pod_ttl, 14400)

    # 사용자 보안 정책 조회 → Pod 생성 시 DB 자격증명 조건부 주입
    from app.schemas.security import SECURITY_TEMPLATES
    user_security = user.security_policy if (user and user.security_policy) else SECURITY_TEMPLATES.get("standard", {})

    # 사용자 인프라 정책 조회 → Pod 리소스/노드 배치 결정
    from app.models.infra_policy import INFRA_TEMPLATES as INFRA_DEFAULTS
    user_infra = user.infra_policy if (user and user.infra_policy) else INFRA_DEFAULTS["standard"]

    # 노드 용량 확인 → 부족하면 노드그룹 스케일업 (비차단)
    _ensure_node_capacity(username, security_policy=user_security, infra_policy=user_infra)

    # K8s Pod 생성 (사용자 프로필 주입 + 동적 TTL + 보안 정책 + 인프라 정책)
    try:
        pod_name, proxy_secret, pod_token_hash = k8s.create_pod(
            username, user_pod_ttl, user_display_name,
            ttl_seconds=ttl_seconds, security_policy=user_security,
            infra_policy=user_infra,
        )
    except K8sServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Pod가 이미 존재하면 proxy_secret=None이 반환됨 — 기존 세션의 proxy_secret 재사용
    if not proxy_secret:
        existing_session = db.query(TerminalSession).filter(
            TerminalSession.pod_name == pod_name,
            TerminalSession.proxy_secret.isnot(None),
        ).order_by(TerminalSession.created_at.desc()).first()
        if existing_session:
            proxy_secret = existing_session.proxy_secret
        else:
            # Fallback: 기존 시크릿이 없으면 새로 생성 (정상 경로에서는 발생하지 않지만 안전망)
            proxy_secret = secrets.token_hex(32)

    # 같은 pod_name의 이전 terminated 세션이 있으면 재활용 (unique constraint 방지)
    old_session = (
        db.query(TerminalSession)
        .filter(TerminalSession.pod_name == pod_name)
        .first()
    )
    if old_session:
        old_session.user_id = current_user["user_id"]
        old_session.pod_status = "creating"
        old_session.session_type = user_pod_ttl
        old_session.started_at = datetime.now(timezone.utc)
        old_session.last_active_at = datetime.now(timezone.utc)
        old_session.terminated_at = None
        if proxy_secret:
            old_session.proxy_secret = proxy_secret
        if pod_token_hash:
            old_session.pod_token_hash = pod_token_hash
        session = old_session
    else:
        session = TerminalSession(
            user_id=current_user["user_id"],
            username=username,
            pod_name=pod_name,
            pod_status="creating",
            session_type=user_pod_ttl,
            proxy_secret=proxy_secret,
            pod_token_hash=pod_token_hash,
        )
        db.add(session)
    db.commit()
    db.refresh(session)

    # Pod 상태 1회 확인 (블로킹 대기 제거 — 프론트엔드 폴링으로 이관)
    import asyncio
    await asyncio.sleep(3)
    pod_status_info = k8s.get_pod_status(pod_name)
    if pod_status_info and pod_status_info["phase"] == "Running":
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
                # TTL 만료 등으로 Pod 종료 → Service/Ingress 정리
                try:
                    k8s.delete_pod(session.pod_name)
                except Exception:
                    pass
    db.commit()

    # 사용자 TTL 조회
    from app.models.user import User
    user = db.query(User).filter(User.username == current_user["sub"]).first()
    ttl = POD_TTL_SECONDS_MAP.get(user.pod_ttl, 14400) if user else 14400

    return SessionListResponse(
        total=len(sessions),
        sessions=[_to_response(s, settings, ttl_seconds=ttl) for s in sessions],
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
                # Pod 없음 → Service/Ingress 고아 리소스 정리
                try:
                    k8s.delete_pod(session.pod_name)
                except Exception:
                    pass
            elif pod_status["phase"] == "Running":
                session.pod_status = "running"
            elif pod_status["phase"] in ("Failed", "Succeeded"):
                session.pod_status = "terminated"
                session.terminated_at = datetime.now(timezone.utc)
                # TTL 만료 등으로 Pod 종료 → Service/Ingress 정리
                try:
                    k8s.delete_pod(session.pod_name)
                except Exception:
                    pass
    db.commit()

    active = [s for s in sessions if s.pod_status in ("creating", "running")]

    # 사용자별 TTL + 이름 조회
    from app.models.user import User
    usernames = {s.username for s in active}
    user_ttls = {}
    user_names = {}
    if usernames:
        users = db.query(User).filter(User.username.in_(usernames)).all()
        user_ttls = {u.username: POD_TTL_SECONDS_MAP.get(u.pod_ttl, 14400) for u in users}
        user_names = {u.username: u.name for u in users}

    return SessionListResponse(
        total=len(active),
        sessions=[
            _to_response(s, settings, ttl_seconds=user_ttls.get(s.username, 14400), user_name=user_names.get(s.username))
            for s in active
        ],
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

    from app.models.infra_policy import INFRA_TEMPLATES as INFRA_DEFAULTS
    bulk_infra = INFRA_DEFAULTS["standard"]

    for username in request.usernames:
        try:
            pod_name, proxy_secret, pod_token_hash = k8s.create_pod(username, request.session_type, infra_policy=bulk_infra)
            # Pod가 이미 존재하면 proxy_secret=None — 기존 세션의 proxy_secret 재사용
            if not proxy_secret:
                existing_sess = db.query(TerminalSession).filter(
                    TerminalSession.pod_name == pod_name,
                    TerminalSession.proxy_secret.isnot(None),
                ).order_by(TerminalSession.created_at.desc()).first()
                if existing_sess:
                    proxy_secret = existing_sess.proxy_secret
                else:
                    proxy_secret = secrets.token_hex(32)
            # 같은 pod_name의 이전 세션 재활용 (unique constraint 방지)
            old_session = (
                db.query(TerminalSession)
                .filter(TerminalSession.pod_name == pod_name)
                .first()
            )
            if old_session:
                old_session.pod_status = "creating"
                old_session.session_type = request.session_type
                old_session.started_at = datetime.now(timezone.utc)
                old_session.last_active_at = datetime.now(timezone.utc)
                old_session.terminated_at = None
                if proxy_secret:
                    old_session.proxy_secret = proxy_secret
                if pod_token_hash:
                    old_session.pod_token_hash = pod_token_hash
                created_sessions.append(old_session)
            else:
                session = TerminalSession(
                    user_id=0,
                    username=username,
                    pod_name=pod_name,
                    pod_status="creating",
                    session_type=request.session_type,
                    proxy_secret=proxy_secret,
                    pod_token_hash=pod_token_hash,
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


# ==================== 사용자 API ====================


@router.post("/internal-heartbeat", status_code=200)
async def internal_heartbeat(
    request: Request,
    db: Session = Depends(get_db),
):
    """Pod 내부에서 호출하는 heartbeat — JWT 불필요, pod_name 기반 인증.

    클러스터 내부 트래픽 전용. ttyd Pod가 5분마다 호출한다.
    Header: X-Pod-Name: claude-terminal-nXXXXXX
    """
    pod_name = request.headers.get("X-Pod-Name", "")
    if not pod_name.startswith("claude-terminal-"):
        raise HTTPException(status_code=400, detail="Invalid pod name")

    session = (
        db.query(TerminalSession)
        .filter(
            TerminalSession.pod_name == pod_name,
            TerminalSession.pod_status.in_(["running", "creating"]),
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # X-Pod-Token 검증: pod_token_hash가 있는 세션만 (이전 세션은 하위 호환)
    pod_token = request.headers.get("X-Pod-Token", "")
    if pod_token and session.pod_token_hash:
        token_hash = hashlib.sha256(pod_token.encode()).hexdigest()
        if token_hash != session.pod_token_hash:
            raise HTTPException(status_code=403, detail="Invalid pod token")

    session.last_active_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "pod_name": pod_name, "last_active_at": session.last_active_at.isoformat()}


@router.post("/heartbeat", status_code=200)
async def heartbeat(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """활성 세션의 마지막 활동 시간 갱신. 프론트엔드가 5분마다 호출."""
    session = (
        db.query(TerminalSession)
        .filter(
            TerminalSession.username == current_user["sub"],
            TerminalSession.pod_status.in_(["running", "creating"]),
        )
        .order_by(TerminalSession.started_at.desc())
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="No active session")
    session.last_active_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "last_active_at": session.last_active_at.isoformat()}


@router.post("/ui-source", status_code=200)
async def record_ui_source(
    body: UiSourceRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """UI 소스 사용 이벤트 기록 — T23.

    Hub 포탈이 webchat ↔ console 탭 전환 시 호출.
    Admin Dashboard /analytics/ui-split 페이지의 주간/월간 집계 기반으로 사용.

    Request:
        source: "webchat" | "console"
    """
    from app.models.ui_source_event import UiSourceEvent
    event = UiSourceEvent(
        username=current_user["sub"],
        source=body.source,
    )
    db.add(event)
    db.commit()
    return {"ok": True, "source": body.source}


def _add_months(dt: datetime, n: int) -> datetime:
    """n개월 후(양수) 또는 전(음수)의 달 첫날 UTC datetime 반환.

    dateutil 없이 순수 산술로 처리.
    """
    total = dt.year * 12 + (dt.month - 1) + n
    year, month_idx = divmod(total, 12)
    return dt.replace(year=year, month=month_idx + 1, day=1)


def _naive(dt: datetime) -> datetime:
    """timezone 정보를 제거하여 naive UTC datetime으로 반환.

    SQLite는 timezone-aware datetime을 문자열 비교로 처리하므로
    SQL 필터 및 Python 비교에서 naive datetime을 사용한다.
    """
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


@router.get("/ui-source/stats", response_model=UiSplitSummary)
async def get_ui_source_stats(
    period: Literal["weekly", "monthly"] = Query(default="weekly"),
    window: int = Query(default=8, ge=1, le=52),
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """UI Split 주간/월간 집계 (관리자 전용) — T23.

    Args:
        period: "weekly" (ISO 월요일 기준) 또는 "monthly"
        window: 반환할 최근 버킷 개수 (1~52, 기본 8)

    Returns:
        UiSplitSummary: 버킷 목록(오래된 것 → 최신) + 전체 기간 요약
    """
    from app.models.ui_source_event import UiSourceEvent

    now = datetime.now(timezone.utc)

    # ── 버킷 경계 목록 생성 (오래된 것 → 최신) ──────────────────────────────
    if period == "weekly":
        # 이번 주 월요일 00:00 UTC
        start_of_current = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        bucket_boundaries = [
            (
                start_of_current - timedelta(weeks=i),
                start_of_current - timedelta(weeks=i) + timedelta(weeks=1),
            )
            for i in range(window - 1, -1, -1)
        ]
    else:  # monthly
        # 이번 달 1일 00:00 UTC
        start_of_current = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        bucket_boundaries = [
            (
                _add_months(start_of_current, -i),
                _add_months(start_of_current, -i + 1),
            )
            for i in range(window - 1, -1, -1)
        ]

    # ── 전체 범위 이벤트 일괄 조회 (SQLite 호환: naive UTC 사용) ─────────────
    oldest_start = _naive(bucket_boundaries[0][0])
    newest_end = _naive(bucket_boundaries[-1][1])

    events = (
        db.query(UiSourceEvent)
        .filter(
            UiSourceEvent.recorded_at >= oldest_start,
            UiSourceEvent.recorded_at < newest_end,
        )
        .all()
    )

    # ── 버킷별 집계 ──────────────────────────────────────────────────────────
    buckets: list[UiSplitBucket] = []
    for bs, be in bucket_boundaries:
        bs_n = _naive(bs)
        be_n = _naive(be)
        bucket_events = [
            e for e in events
            if bs_n <= _naive(e.recorded_at) < be_n
        ]
        buckets.append(
            UiSplitBucket(
                period_start=bs_n.date(),
                period_end=be_n.date(),
                webchat_users=len({e.username for e in bucket_events if e.source == "webchat"}),
                console_users=len({e.username for e in bucket_events if e.source == "console"}),
                total_events=len(bucket_events),
            )
        )

    # ── 전체 기간 요약 ────────────────────────────────────────────────────────
    all_webchat = {e.username for e in events if e.source == "webchat"}
    all_console = {e.username for e in events if e.source == "console"}
    both = all_webchat & all_console

    return UiSplitSummary(
        period=period,
        window=window,
        webchat_total_users=len(all_webchat),
        console_total_users=len(all_console),
        both_users=len(both),
        webchat_only_users=len(all_webchat - all_console),
        console_only_users=len(all_console - all_webchat),
        buckets=buckets,
    )


@router.delete("/", response_model=SessionResponse)
async def terminate_my_session(
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    k8s: K8sService = Depends(_get_k8s_service),
    db: Session = Depends(get_db),
):
    """내 활성 세션 종료 (Pod 삭제). Hub 포탈의 '로그아웃 & 종료' 버튼용."""
    username = current_user["sub"]
    session = (
        db.query(TerminalSession)
        .filter(
            TerminalSession.username == username,
            TerminalSession.pod_status.in_(["running", "creating"]),
        )
        .order_by(TerminalSession.started_at.desc())
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Active session not found")

    if session.pod_name and session.pod_status != "terminated":
        try:
            k8s.delete_pod(session.pod_name)
        except K8sServiceError as e:
            logger.error(f"Failed to delete pod: {e}")

    session.pod_status = "terminated"
    session.terminated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)

    logger.info(f"User {username} terminated own session: {session.pod_name}")
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
