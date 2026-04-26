"""Admin-only API: token usage analytics + infrastructure status + token usage daily tracking."""
import base64
import logging
import re
from datetime import datetime, timezone, date as date_type, timedelta

from fastapi import APIRouter, Depends, HTTPException
from kubernetes import client, config as k8s_config
from kubernetes.stream import stream

# K8s client 초기화 (incluster → kubeconfig → CI no-op)
try:
    k8s_config.load_incluster_config()
except k8s_config.ConfigException:
    try:
        k8s_config.load_kube_config()
    except k8s_config.ConfigException:
        pass  # CI / test environment: no cluster available
from pydantic import BaseModel

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core import pricing as _pricing
from app.core.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ==================== Token Usage ====================

class UserTokenUsage(BaseModel):
    username: str
    user_name: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cost_krw: int = 0


class TokenUsageResponse(BaseModel):
    users: list[UserTokenUsage]
    total_input: int = 0
    total_output: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_cost_krw: int = 0
    collected_at: str


_LEGACY_PRICE = _pricing.get_price_table("claude-sonnet-4-6")


def _collect_tokens_from_pod(v1: client.CoreV1Api, pod_name: str, namespace: str) -> tuple[int, int]:
    """Execute token count script inside pod, return (input_tokens, output_tokens)."""
    script = (
        'import re,glob;ti=to=0\n'
        'for f in glob.glob("/home/node/.claude/projects/-home-node/*.jsonl"):\n'
        ' c=open(f).read()\n'
        ' for m in re.finditer(r\'"input_tokens":(\\d+)\',c):ti+=int(m.group(1))\n'
        ' for m in re.finditer(r\'"output_tokens":(\\d+)\',c):to+=int(m.group(1))\n'
        'print(f"{ti},{to}")'
    )
    try:
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name, namespace,
            command=["python3", "-c", script],
            container="terminal",
            stderr=False, stdin=False, stdout=True, tty=False,
        )
        parts = resp.strip().split(",")
        return int(parts[0]), int(parts[1])
    except Exception as e:
        logger.warning(f"Token collection failed for {pod_name}: {e}")
        return 0, 0


@router.get("/token-usage", response_model=TokenUsageResponse)
async def get_token_usage(
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """Collect token usage from all running pods."""
    from app.core.database import SessionLocal
    from app.models.user import User

    v1 = client.CoreV1Api()
    namespace = settings.k8s_namespace

    pods = v1.list_namespaced_pod(
        namespace=namespace, label_selector="app=claude-terminal",
        field_selector="status.phase=Running",
    )

    db = SessionLocal()
    users_db = {u.username: u.name for u in db.query(User).all()}
    db.close()

    user_usages = []
    for pod in pods.items:
        pod_name = pod.metadata.name
        username = pod_name.replace("claude-terminal-", "").upper()

        input_tokens, output_tokens = _collect_tokens_from_pod(v1, pod_name, namespace)
        total = input_tokens + output_tokens
        # T20 활성화 전 legacy snapshot 경로 — 모델 정보 없어 Sonnet 가격으로 추정
        cost_usd = round(
            (input_tokens * _LEGACY_PRICE["input"]
             + output_tokens * _LEGACY_PRICE["output"]) / 1_000_000,
            6,
        )
        cost_krw = round(cost_usd * _pricing.KRW_RATE)

        user_usages.append(UserTokenUsage(
            username=username,
            user_name=users_db.get(username),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            cost_usd=cost_usd,
            cost_krw=cost_krw,
        ))

    user_usages.sort(key=lambda x: x.total_tokens, reverse=True)

    # 실시간 호출 시 hourly 테이블도 함께 업데이트 (스파크라인 실시간 반영)
    try:
        from app.models.token_usage import TokenUsageHourly
        now = datetime.now(timezone.utc)
        today = now.date()
        current_hour = now.hour
        current_slot = now.hour * 6 + now.minute // 10

        db2 = SessionLocal()
        for u in user_usages:
            if u.total_tokens == 0:
                continue
            existing = db2.query(TokenUsageHourly).filter(
                TokenUsageHourly.username == u.username,
                TokenUsageHourly.usage_date == today,
                TokenUsageHourly.slot == current_slot,
            ).first()
            if existing:
                existing.input_tokens = u.input_tokens
                existing.output_tokens = u.output_tokens
                existing.total_tokens = u.total_tokens
                existing.cost_usd = u.cost_usd
                existing.cost_krw = u.cost_krw
            else:
                db2.add(TokenUsageHourly(
                    username=u.username, usage_date=today,
                    hour=current_hour, slot=current_slot,
                    input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                    total_tokens=u.total_tokens, cost_usd=u.cost_usd, cost_krw=u.cost_krw,
                ))
        db2.commit()
        db2.close()
    except Exception as e:
        logger.warning(f"Realtime hourly upsert failed: {e}")

    return TokenUsageResponse(
        users=user_usages,
        total_input=sum(u.input_tokens for u in user_usages),
        total_output=sum(u.output_tokens for u in user_usages),
        total_tokens=sum(u.total_tokens for u in user_usages),
        total_cost_usd=round(sum(u.cost_usd for u in user_usages), 4),
        total_cost_krw=sum(u.cost_krw for u in user_usages),
        collected_at=datetime.now(timezone.utc).isoformat(),
    )


# ==================== Pricing Reference ====================

class ModelPricing(BaseModel):
    model_id: str
    display_name: str
    input_usd: float
    output_usd: float
    cache_creation_usd: float
    cache_read_usd: float
    input_krw: int
    output_krw: int


class PricingResponse(BaseModel):
    models: list[ModelPricing]
    krw_rate: int
    unit: str = "per 1M tokens"
    as_of: str


@router.get("/pricing", response_model=PricingResponse)
async def get_pricing(_admin: dict = Depends(_require_admin)):
    """Return current Bedrock model pricing table."""
    _DISPLAY = {
        "claude-sonnet-4-6": "Claude Sonnet 4.6",
        "claude-haiku-4-5": "Claude Haiku 4.5",
        "claude-opus-4-6": "Claude Opus 4.6",
    }
    models = []
    for model_id, prices in _pricing.PRICE_TABLE.items():
        models.append(ModelPricing(
            model_id=model_id,
            display_name=_DISPLAY.get(model_id, model_id),
            input_usd=prices["input"],
            output_usd=prices["output"],
            cache_creation_usd=prices["cache_creation"],
            cache_read_usd=prices["cache_read"],
            input_krw=round(prices["input"] * _pricing.KRW_RATE),
            output_krw=round(prices["output"] * _pricing.KRW_RATE),
        ))
    return PricingResponse(
        models=models,
        krw_rate=_pricing.KRW_RATE,
        as_of="2026-04-25",
    )


# ==================== Infrastructure ====================

class PodInfo(BaseModel):
    pod_name: str
    username: str
    user_name: str | None = None
    status: str
    node_name: str
    cpu_request: str
    memory_request: str
    created_at: str | None = None
    pod_ip: str | None = None
    namespace: str | None = None
    pod_kind: str = "terminal"  # "terminal" | "workload" | "system" | "dummy"


class NodeInfo(BaseModel):
    node_name: str
    instance_type: str
    status: str
    cpu_capacity: str
    memory_capacity: str
    node_role: str = "user"  # "system" | "presenter" | "user" | "workload"
    pods: list[PodInfo]


class InfraResponse(BaseModel):
    nodes: list[NodeInfo]
    total_nodes: int
    total_pods: int
    collected_at: str


@router.get("/infrastructure", response_model=InfraResponse)
async def get_infrastructure(
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """Get real-time node and pod infrastructure status."""
    from app.core.database import SessionLocal
    from app.models.user import User

    v1 = client.CoreV1Api()
    namespace = settings.k8s_namespace

    db = SessionLocal()
    users_db = {u.username: u.name for u in db.query(User).all()}
    db.close()

    # Nodes
    nodes = v1.list_node()
    node_map = {}
    for node in nodes.items:
        name = node.metadata.name
        labels = node.metadata.labels or {}
        instance_type = labels.get("node.kubernetes.io/instance-type", "unknown")
        status = "Ready" if any(
            c.type == "Ready" and c.status == "True"
            for c in (node.status.conditions or [])
        ) else "NotReady"

        # cordon된 노드 (종료 대기 중) 필터링
        if node.spec.unschedulable:
            continue

        # 노드 역할 판별
        node_label_role = labels.get("role", "")
        nodegroup = labels.get("eks.amazonaws.com/nodegroup", "")
        if node_label_role == "presenter":
            node_role = "presenter"
        elif node_label_role == "system" or nodegroup in ("system-node-large",):
            node_role = "system"
        elif node_label_role in ("user-apps",) or nodegroup.endswith("user-apps-workers"):
            # t3.medium 사용자 배포 앱 전용 노드 (bin-packing)
            node_role = "user-apps"
        elif node_label_role in ("claude-terminal",) or nodegroup in ("bedrock-claude-nodes", "claude-workers"):
            # m5.large 워크로드 노드 — openwebui 등 공유 서비스
            node_role = "workload"
        elif node_label_role in ("claude-dedicated",) or nodegroup in ("bedrock-claude-dedicated-nodes",):
            # 사용자 터미널 전용 노드 (nodeSelector: role=claude-dedicated)
            node_role = "terminal"
        elif node_label_role == "ingress" or nodegroup in ("ingress-workers",):
            node_role = "system"
        elif node_label_role == "gitea" or nodegroup.endswith("gitea-workers"):
            # gitea-workers: valkey-cluster + onlyoffice 전용 — 사용자 터미널 배치 대상 제외
            node_role = "gitea"
        else:
            node_role = "user"

        node_map[name] = NodeInfo(
            node_name=name,
            instance_type=instance_type,
            status=status,
            cpu_capacity=node.status.capacity.get("cpu", "0"),
            memory_capacity=node.status.capacity.get("memory", "0"),
            node_role=node_role,
            pods=[],
        )

    # Platform system pods (auth-gateway, platform-db 등)
    platform_pods = v1.list_namespaced_pod(namespace="platform")
    for pod in platform_pods.items:
        node_name = pod.spec.node_name or "unscheduled"
        if node_name in node_map:
            node_map[node_name].node_role = "system"
            pod_info = PodInfo(
                pod_name=pod.metadata.name,
                username="SYSTEM",
                user_name=pod.metadata.name.split("-")[0],  # auth-gateway, platform-db
                status=pod.status.phase or "Unknown",
                node_name=node_name,
                cpu_request="system",
                memory_request="system",
                created_at=pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
            )
            node_map[node_name].pods.append(pod_info)

    # Ingress controller pods
    try:
        ingress_pods = v1.list_namespaced_pod(namespace="ingress-nginx")
        for pod in ingress_pods.items:
            node_name = pod.spec.node_name or "unscheduled"
            if node_name in node_map:
                node_map[node_name].node_role = "system"
                pod_info = PodInfo(
                    pod_name=pod.metadata.name,
                    username="SYSTEM",
                    user_name="ingress-nginx",
                    status=pod.status.phase or "Unknown",
                    node_name=node_name,
                    cpu_request="system",
                    memory_request="system",
                    created_at=pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
                )
                node_map[node_name].pods.append(pod_info)
    except Exception:
        pass

    # Workload namespace pods (openwebui, claude-apps)
    # claude-sessions 제외: 해당 ns에는 사용자 터미널 Pod가 포함되어 있어 아래 terminal 스캔과 중복됨
    _WORKLOAD_NAMESPACES = ("openwebui", "claude-apps")
    for wl_ns in _WORKLOAD_NAMESPACES:
        try:
            wl_pods = v1.list_namespaced_pod(namespace=wl_ns)
        except Exception:
            continue
        for pod in wl_pods.items:
            node_name = pod.spec.node_name or "unscheduled"
            if node_name not in node_map:
                continue
            labels_pod = pod.metadata.labels or {}
            # 터미널/더미 Pod는 아래 terminal 스캔에서 별도 처리 — 중복 방지
            if labels_pod.get("app") in ("claude-terminal", "overprovisioning"):
                continue
            app_label = labels_pod.get("app-name") or labels_pod.get("app") or pod.metadata.name.rsplit("-", 2)[0]
            container = pod.spec.containers[0] if pod.spec.containers else None
            cpu_req = mem_req = "-"
            if container and container.resources and container.resources.requests:
                cpu_req = container.resources.requests.get("cpu", "-")
                mem_req = container.resources.requests.get("memory", "-")
            pod_info = PodInfo(
                pod_name=pod.metadata.name,
                username="WORKLOAD",
                user_name=app_label,
                status=pod.status.phase or "Unknown",
                node_name=node_name,
                cpu_request=cpu_req,
                memory_request=mem_req,
                created_at=pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
                pod_ip=pod.status.pod_ip,
                namespace=wl_ns,
                pod_kind="workload",
            )
            node_map[node_name].pods.append(pod_info)
            # user-apps / terminal / system 노드 역할은 유지 — workload로 덮어쓰지 않음
            if node_map[node_name].node_role not in ("system", "user-apps", "terminal"):
                node_map[node_name].node_role = "workload"

    # User terminal pods
    pods = v1.list_namespaced_pod(namespace=namespace, label_selector="app=claude-terminal")
    total_pods = 0
    for pod in pods.items:
        pod_name = pod.metadata.name
        username = pod_name.replace("claude-terminal-", "").upper()
        node_name = pod.spec.node_name or "unscheduled"

        container = pod.spec.containers[0] if pod.spec.containers else None
        cpu_req = "0"
        mem_req = "0"
        if container and container.resources and container.resources.requests:
            cpu_req = container.resources.requests.get("cpu", "0")
            mem_req = container.resources.requests.get("memory", "0")

        pod_info = PodInfo(
            pod_name=pod_name,
            username=username,
            user_name=users_db.get(username),
            status=pod.status.phase or "Unknown",
            node_name=node_name,
            cpu_request=cpu_req,
            memory_request=mem_req,
            created_at=pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
            pod_ip=pod.status.pod_ip,
            namespace=namespace,
            pod_kind="terminal",
        )

        if node_name in node_map:
            node_map[node_name].pods.append(pod_info)
        total_pods += 1

    # Overprovisioning (더미) pods — 노드 사전 확보용
    dummy_pods = v1.list_namespaced_pod(namespace=namespace, label_selector="app=overprovisioning")
    for pod in dummy_pods.items:
        pod_name = pod.metadata.name
        node_name = pod.spec.node_name or "unscheduled"
        pod_info = PodInfo(
            pod_name=pod_name,
            username="DUMMY",
            user_name="예약석 (더미)",
            status=pod.status.phase or "Unknown",
            node_name=node_name,
            cpu_request="500m",
            memory_request="1536Mi",
            created_at=pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
        )
        if node_name in node_map:
            node_map[node_name].pods.append(pod_info)

    node_list = sorted(node_map.values(), key=lambda n: (-len(n.pods), n.node_name))

    return InfraResponse(
        nodes=node_list,
        total_nodes=len(node_list),
        total_pods=total_pods,
        collected_at=datetime.now(timezone.utc).isoformat(),
    )


# ==================== Pod Management ====================

class AssignPodRequest(BaseModel):
    username: str
    node_name: str | None = None  # 특정 노드 지정 (None이면 자동 배치)


class MovePodRequest(BaseModel):
    username: str
    target_node: str


class PodActionResponse(BaseModel):
    username: str
    pod_name: str
    status: str
    node_name: str | None = None


@router.post("/assign-pod", response_model=PodActionResponse)
async def assign_pod(
    req: AssignPodRequest,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """사용자에게 Pod을 할당 (특정 노드 지정 가능)."""
    from app.core.database import SessionLocal
    from app.models.user import User
    from app.models.session import TerminalSession
    from app.services.k8s_service import K8sService

    db = SessionLocal()
    user = db.query(User).filter(User.username == req.username.upper()).first()
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if not user.is_approved:
        db.close()
        raise HTTPException(status_code=400, detail="미승인 사용자입니다")

    v1 = client.CoreV1Api()
    namespace = settings.k8s_namespace
    pod_name = f"claude-terminal-{req.username.lower()}"

    # 기존 Pod 확인
    try:
        existing = v1.read_namespaced_pod(pod_name, namespace)
        if existing.status.phase in ("Running", "Pending"):
            db.close()
            raise HTTPException(status_code=400, detail=f"이미 실행 중인 Pod이 있습니다 ({existing.status.phase})")
    except client.rest.ApiException as e:
        if e.status != 404:
            raise

    k8s = K8sService(settings)

    from app.schemas.user import POD_TTL_SECONDS_MAP
    from app.schemas.security import SECURITY_TEMPLATES
    from app.models.infra_policy import INFRA_TEMPLATES as INFRA_DEFAULTS
    ttl = POD_TTL_SECONDS_MAP.get(user.pod_ttl, 14400)
    user_security = user.security_policy if user.security_policy else SECURITY_TEMPLATES.get("standard", {})
    user_infra = user.infra_policy if user.infra_policy else INFRA_DEFAULTS["standard"]
    pod_name, proxy_secret, pod_token_hash = k8s.create_pod(
        req.username.upper(), "daily", user.name or req.username,
        ttl_seconds=ttl, target_node=req.node_name,
        security_policy=user_security,
        infra_policy=user_infra,
    )

    # 세션 레코드
    session = db.query(TerminalSession).filter(TerminalSession.pod_name == pod_name).first()
    if session:
        session.pod_status = "creating"
        session.started_at = datetime.now(timezone.utc)
        session.terminated_at = None
        if proxy_secret:
            session.proxy_secret = proxy_secret
        if pod_token_hash:
            session.pod_token_hash = pod_token_hash
    else:
        session = TerminalSession(
            user_id=user.id, username=req.username.upper(),
            pod_name=pod_name, pod_status="creating",
            session_type="daily", started_at=datetime.now(timezone.utc),
            proxy_secret=proxy_secret,
            pod_token_hash=pod_token_hash,
        )
        db.add(session)
    db.commit()
    db.close()

    return PodActionResponse(
        username=req.username.upper(),
        pod_name=pod_name,
        status="creating",
        node_name=req.node_name,
    )



@router.post("/move-pod", response_model=PodActionResponse)
async def move_pod(
    req: MovePodRequest,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """사용자 Pod을 다른 노드로 이동 (백업 → 삭제 → 재생성)."""
    from app.core.database import SessionLocal
    from app.models.user import User
    from app.models.session import TerminalSession
    from app.services.k8s_service import K8sService

    v1 = client.CoreV1Api()
    namespace = settings.k8s_namespace
    pod_name = f"claude-terminal-{req.username.lower()}"

    # 기존 Pod 백업 실행
    try:
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name, namespace,
            command=["bash", "-c",
                     "mkdir -p /home/node/workspace/.claude-backup && "
                     "cp -r /home/node/.claude/projects/ /home/node/workspace/.claude-backup/ 2>/dev/null; "
                     "cp /home/node/.claude/history.jsonl /home/node/workspace/.claude-backup/ 2>/dev/null; "
                     "cp -r /home/node/.serena/ /home/node/workspace/.serena-backup/ 2>/dev/null; "
                     "echo OK"],
            container="terminal",
            stderr=False, stdin=False, stdout=True, tty=False,
        )
        logger.info(f"Backup for {pod_name}: {resp.strip()}")
    except Exception as e:
        logger.warning(f"Backup failed for {pod_name}: {e}")

    # 삭제
    k8s = K8sService(settings)
    k8s.delete_pod(pod_name)

    import time
    time.sleep(3)

    # DB에서 사용자 정보
    db = SessionLocal()
    user = db.query(User).filter(User.username == req.username.upper()).first()
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

    # 대상 노드에 재생성 (보안 정책 + 인프라 정책 유지)
    from app.schemas.user import POD_TTL_SECONDS_MAP
    from app.schemas.security import SECURITY_TEMPLATES
    from app.models.infra_policy import INFRA_TEMPLATES as INFRA_DEFAULTS
    ttl = POD_TTL_SECONDS_MAP.get(user.pod_ttl, 14400)
    user_security = user.security_policy if user.security_policy else SECURITY_TEMPLATES.get("standard", {})
    user_infra = user.infra_policy if user.infra_policy else INFRA_DEFAULTS["standard"]
    _, move_proxy_secret, move_pod_token_hash = k8s.create_pod(
        req.username.upper(), "daily", user.name or req.username,
        ttl_seconds=ttl, target_node=req.target_node,
        security_policy=user_security,
        infra_policy=user_infra,
    )

    # 세션 업데이트
    session = db.query(TerminalSession).filter(TerminalSession.pod_name == pod_name).first()
    if session:
        session.pod_status = "creating"
        session.started_at = datetime.now(timezone.utc)
        session.terminated_at = None
        if move_proxy_secret:
            session.proxy_secret = move_proxy_secret
        if move_pod_token_hash:
            session.pod_token_hash = move_pod_token_hash
    db.commit()
    db.close()

    return PodActionResponse(
        username=req.username.upper(),
        pod_name=pod_name,
        status="creating",
        node_name=req.target_node,
    )


@router.delete("/terminate-pod/{username}")
async def terminate_pod(
    username: str,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """사용자 Pod 종료."""
    from app.core.database import SessionLocal
    from app.models.session import TerminalSession
    from app.services.k8s_service import K8sService

    v1 = client.CoreV1Api()
    namespace = settings.k8s_namespace
    pod_name = f"claude-terminal-{username.lower()}"

    # 백업
    try:
        stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name, namespace,
            command=["bash", "-c",
                     "mkdir -p /home/node/workspace/.claude-backup && "
                     "cp -r /home/node/.claude/projects/ /home/node/workspace/.claude-backup/ 2>/dev/null; "
                     "cp /home/node/.claude/history.jsonl /home/node/workspace/.claude-backup/ 2>/dev/null; "
                     "echo OK"],
            container="terminal",
            stderr=False, stdin=False, stdout=True, tty=False,
        )
    except Exception:
        pass

    k8s = K8sService(settings)
    k8s.delete_pod(pod_name)

    db = SessionLocal()
    session = db.query(TerminalSession).filter(TerminalSession.pod_name == pod_name).first()
    if session:
        session.pod_status = "terminated"
        session.terminated_at = datetime.now(timezone.utc)
        session.terminate_reason = "admin forced termination"
    db.commit()
    db.close()

    return {"username": username.upper(), "pod_name": pod_name, "status": "terminated"}


# ==================== Node Group Scaling ====================

class NodeGroupInfo(BaseModel):
    name: str
    instance_type: str
    min_size: int
    max_size: int
    desired_size: int
    status: str


class NodeGroupListResponse(BaseModel):
    groups: list[NodeGroupInfo]


class ScaleNodeGroupRequest(BaseModel):
    nodegroup_name: str
    desired_size: int


def _get_eks_client():
    import boto3
    return boto3.client("eks", region_name="ap-northeast-2")


@router.get("/nodegroups", response_model=NodeGroupListResponse)
async def list_nodegroups(
    _admin: dict = Depends(_require_admin),
):
    """EKS 노드그룹 목록 조회.

    개별 nodegroup describe 시 IAM 권한 누락 등 예외가 발생해도 500으로 떨어뜨리지 않고,
    가능한 그룹만 반환한다. 실패한 그룹은 status='unknown' placeholder로 포함.
    """
    eks = _get_eks_client()
    cluster = "bedrock-claude-eks"

    try:
        ng_names = eks.list_nodegroups(clusterName=cluster)["nodegroups"]
    except Exception as e:
        logger.error(f"list_nodegroups failed: {e}")
        return NodeGroupListResponse(groups=[])

    groups = []
    for ng_name in ng_names:
        try:
            ng = eks.describe_nodegroup(clusterName=cluster, nodegroupName=ng_name)["nodegroup"]
            scaling = ng["scalingConfig"]
            instance_types = ng.get("instanceTypes", ["unknown"])
            groups.append(NodeGroupInfo(
                name=ng_name,
                instance_type=instance_types[0] if instance_types else "unknown",
                min_size=scaling["minSize"],
                max_size=scaling["maxSize"],
                desired_size=scaling["desiredSize"],
                status=ng["status"],
            ))
        except Exception as e:
            logger.warning(f"describe_nodegroup({ng_name}) failed: {e}")
            groups.append(NodeGroupInfo(
                name=ng_name,
                instance_type="unknown",
                min_size=0,
                max_size=0,
                desired_size=0,
                status="describe_failed",
            ))
    return NodeGroupListResponse(groups=groups)


@router.post("/scale-nodegroup")
async def scale_nodegroup(
    req: ScaleNodeGroupRequest,
    _admin: dict = Depends(_require_admin),
):
    """EKS 노드그룹 스케일링."""
    eks = _get_eks_client()
    cluster = "bedrock-claude-eks"

    # 현재 상태 확인
    ng = eks.describe_nodegroup(clusterName=cluster, nodegroupName=req.nodegroup_name)["nodegroup"]
    scaling = ng["scalingConfig"]

    if req.desired_size < 0:
        raise HTTPException(status_code=400, detail="노드 수는 0 이상이어야 합니다")
    if req.desired_size > scaling["maxSize"]:
        raise HTTPException(status_code=400, detail=f"최대 {scaling['maxSize']}대까지 가능합니다")

    # 시스템 보호: 시스템/인프라 Pod이 있는 노드그룹은 축소 차단
    v1 = client.CoreV1Api()
    ng_nodes = set()
    nodes = v1.list_node()
    for node in nodes.items:
        labels = node.metadata.labels or {}
        # 노드가 이 노드그룹에 속하는지 확인 (instance-type + nodegroup label)
        ng_label = labels.get("eks.amazonaws.com/nodegroup", "")
        if ng_label == req.nodegroup_name:
            ng_nodes.add(node.metadata.name)

    # 시스템 Pod 확인
    has_system_pods = False
    for ns in ["platform", "ingress-nginx"]:
        try:
            pods = v1.list_namespaced_pod(namespace=ns)
            for pod in pods.items:
                if pod.spec.node_name in ng_nodes:
                    has_system_pods = True
                    break
        except Exception:
            pass
        if has_system_pods:
            break

    if has_system_pods and req.desired_size < len(ng_nodes):
        # 시스템 Pod이 있는 노드그룹은 현재 노드 수 이하로 축소 금지
        raise HTTPException(
            status_code=400,
            detail=f"시스템 Pod(Auth Gateway, Ingress)이 이 노드그룹에서 운영 중입니다. "
                   f"현재 {len(ng_nodes)}대 미만으로 축소할 수 없습니다.",
        )

    new_min = min(scaling["minSize"], req.desired_size)

    eks.update_nodegroup_config(
        clusterName=cluster,
        nodegroupName=req.nodegroup_name,
        scalingConfig={
            "minSize": new_min,
            "maxSize": int(scaling["maxSize"]),
            "desiredSize": req.desired_size,
        },
    )

    logger.info(f"Nodegroup {req.nodegroup_name} scaled to {req.desired_size}")
    return {
        "nodegroup": req.nodegroup_name,
        "desired_size": req.desired_size,
        "status": "scaling",
    }


class DrainNodeRequest(BaseModel):
    node_name: str


@router.post("/drain-node")
async def drain_node(
    req: DrainNodeRequest,
    _admin: dict = Depends(_require_admin),
):
    """특정 노드를 drain하고 제거 (Pod 없는 노드만 가능)."""
    v1 = client.CoreV1Api()

    # 노드 존재 확인
    try:
        node = v1.read_node(req.node_name)
    except client.rest.ApiException:
        raise HTTPException(status_code=404, detail="노드를 찾을 수 없습니다")

    # 보호 대상 역할/네임스페이스 정의
    _PROTECTED_ROLES = {"system", "ingress", "gitea"}
    _PROTECTED_NAMESPACES = {"platform", "ingress-nginx", "gitea"}
    _SYSTEM_POD_PREFIXES = ("aws-node", "kube-proxy", "efs-csi", "coredns")

    labels = node.metadata.labels or {}
    node_role = labels.get("role", "")
    if node_role in _PROTECTED_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"이 노드({node_role} 역할)는 보호 대상으로 제거할 수 없습니다",
        )

    # 사용자/플랫폼 Pod 확인 — 보호 네임스페이스 Pod이 있으면 차단
    all_pods = v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={req.node_name}")
    non_system_pods = [
        p for p in all_pods.items
        if p.metadata.namespace not in ("kube-system",)
        and p.metadata.namespace not in _PROTECTED_NAMESPACES
        and not any(p.metadata.name.startswith(prefix) for prefix in _SYSTEM_POD_PREFIXES)
    ]

    if non_system_pods:
        pod_names = [f"{p.metadata.namespace}/{p.metadata.name}" for p in non_system_pods]
        raise HTTPException(
            status_code=400,
            detail=f"노드에 Pod이 실행 중입니다: {', '.join(pod_names)}. 먼저 Pod을 종료하세요.",
        )

    # 노드 cordon (스케줄링 차단)
    body = {"spec": {"unschedulable": True}}
    v1.patch_node(req.node_name, body)

    # 노드그룹 확인 후 desired -1
    ng_name = labels.get("eks.amazonaws.com/nodegroup", "")
    if ng_name:
        eks = _get_eks_client()
        cluster = "bedrock-claude-eks"
        ng = eks.describe_nodegroup(clusterName=cluster, nodegroupName=ng_name)["nodegroup"]
        current = ng["scalingConfig"]["desiredSize"]
        new_desired = max(0, current - 1)
        new_min = min(ng["scalingConfig"]["minSize"], new_desired)
        eks.update_nodegroup_config(
            clusterName=cluster,
            nodegroupName=ng_name,
            scalingConfig={
                "minSize": new_min,
                "maxSize": int(ng["scalingConfig"]["maxSize"]),
                "desiredSize": new_desired,
            },
        )
        logger.info(f"Node {req.node_name} cordoned, {ng_name} scaled to {new_desired}")
        return {"node_name": req.node_name, "nodegroup": ng_name, "new_desired": new_desired, "status": "draining"}

    raise HTTPException(status_code=400, detail="노드그룹을 확인할 수 없습니다")


# ==================== Auto Scale-Down ====================

@router.post("/auto-scale-down")
async def auto_scale_down(
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """사용자 Pod 없는 노드 자동 축소 (system-node 보호)."""
    v1 = client.CoreV1Api()

    # Get all nodes
    nodes = v1.list_node().items

    # Get all user pods
    user_pods = v1.list_namespaced_pod(
        namespace=settings.k8s_namespace,
        label_selector="app=claude-terminal",
    ).items

    # Map: node_name -> user pod count
    node_pod_count = {}
    for pod in user_pods:
        if pod.status.phase in ("Running", "Pending"):
            node = pod.spec.node_name or "unscheduled"
            node_pod_count[node] = node_pod_count.get(node, 0) + 1

    # Find empty non-system nodes
    scaled_down = []
    for node in nodes:
        name = node.metadata.name
        labels = node.metadata.labels or {}
        role = labels.get("role", "")

        # Skip system nodes
        if role == "system":
            continue

        # Skip if node is already cordoned/unschedulable
        if node.spec.unschedulable:
            continue

        # Skip if node has user pods
        if node_pod_count.get(name, 0) > 0:
            continue

        # Check if node has platform/ingress pods (system protection)
        all_pods = v1.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={name}"
        ).items
        has_system_pods = any(
            p.metadata.namespace in ("platform", "ingress-nginx")
            for p in all_pods
        )
        if has_system_pods:
            logger.info(f"Node {name} has system pods, skipping scale-down")
            continue

        # This node is empty and safe to remove
        ng_name = labels.get("eks.amazonaws.com/nodegroup", "")
        if not ng_name:
            continue

        try:
            # Cordon the node
            body = {"spec": {"unschedulable": True}}
            v1.patch_node(name, body)

            # Scale down nodegroup
            eks = _get_eks_client()
            cluster_name = "bedrock-claude-eks"
            ng = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)["nodegroup"]
            current = ng["scalingConfig"]["desiredSize"]
            new_desired = max(0, current - 1)
            new_min = min(ng["scalingConfig"]["minSize"], new_desired)

            eks.update_nodegroup_config(
                clusterName=cluster_name,
                nodegroupName=ng_name,
                scalingConfig={
                    "minSize": new_min,
                    "maxSize": int(ng["scalingConfig"]["maxSize"]),
                    "desiredSize": new_desired,
                },
            )

            scaled_down.append({"node": name, "nodegroup": ng_name, "new_desired": new_desired})
            logger.info(f"Auto scale-down: {name} ({ng_name}) → {new_desired}")

        except Exception as e:
            logger.error(f"Failed to scale down {name}: {e}")

    return {"scaled_down": scaled_down, "checked_nodes": len(nodes)}


# ==================== Unhealthy Pods ====================

class UnhealthyPod(BaseModel):
    namespace: str
    pod_name: str
    pod_ip: str | None = None
    node_name: str | None = None
    status: str
    reason: str | None = None
    restarts: int = 0
    age_seconds: int = 0
    owner: str | None = None
    app_name: str | None = None
    deployment: str | None = None
    message: str | None = None


class UnhealthyPodsResponse(BaseModel):
    pods: list[UnhealthyPod]
    collected_at: str


_UNHEALTHY_NAMESPACES = ("claude-apps", "claude-sessions", "openwebui")
_RESTART_THRESHOLD = 5


@router.get("/infra/unhealthy-pods", response_model=UnhealthyPodsResponse)
async def list_unhealthy_pods(
    _admin: dict = Depends(_require_admin),
):
    """비정상 Pod 실시간 조회 (CrashLoopBackOff, ImagePullBackOff, Pending 장기화 등)."""
    v1 = client.CoreV1Api()
    now = datetime.now(timezone.utc)
    unhealthy: list[UnhealthyPod] = []

    for ns in _UNHEALTHY_NAMESPACES:
        try:
            pods = v1.list_namespaced_pod(namespace=ns)
        except Exception as e:
            logger.warning(f"list_namespaced_pod({ns}) failed: {e}")
            continue

        for pod in pods.items:
            phase = pod.status.phase or "Unknown"
            statuses = pod.status.container_statuses or []
            restarts = sum(s.restart_count for s in statuses)

            bad_reason: str | None = None
            bad_message: str | None = None
            for s in statuses:
                waiting = getattr(s.state, "waiting", None)
                if waiting and waiting.reason in (
                    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
                    "CreateContainerConfigError", "CreateContainerError",
                    "RunContainerError", "InvalidImageName",
                ):
                    bad_reason = waiting.reason
                    bad_message = waiting.message
                    break

            is_unhealthy = (
                bad_reason is not None
                or phase in ("Failed", "Unknown")
                or restarts >= _RESTART_THRESHOLD
                or (phase == "Pending" and pod.metadata.creation_timestamp
                    and (now - pod.metadata.creation_timestamp).total_seconds() > 300)
            )
            if not is_unhealthy:
                continue

            labels = pod.metadata.labels or {}
            age = 0
            if pod.metadata.creation_timestamp:
                age = int((now - pod.metadata.creation_timestamp).total_seconds())

            # deployment 이름 추출 (ReplicaSet owner → deployment 접두부)
            deployment = None
            for ref in (pod.metadata.owner_references or []):
                if ref.kind == "ReplicaSet":
                    # rs name: <deploy>-<hash>
                    deployment = ref.name.rsplit("-", 1)[0]
                    break

            unhealthy.append(UnhealthyPod(
                namespace=ns,
                pod_name=pod.metadata.name,
                pod_ip=pod.status.pod_ip,
                node_name=pod.spec.node_name,
                status=bad_reason or phase,
                reason=bad_reason,
                restarts=restarts,
                age_seconds=age,
                owner=labels.get("owner"),
                app_name=labels.get("app-name") or labels.get("app"),
                deployment=deployment,
                message=(bad_message[:200] if bad_message else None),
            ))

    unhealthy.sort(key=lambda p: (-p.restarts, p.namespace, p.pod_name))
    return UnhealthyPodsResponse(
        pods=unhealthy,
        collected_at=now.isoformat(),
    )


class DeleteDeploymentRequest(BaseModel):
    namespace: str
    deployment: str


@router.post("/infra/delete-deployment")
async def delete_unhealthy_deployment(
    req: DeleteDeploymentRequest,
    _admin: dict = Depends(_require_admin),
):
    """불량 Deployment + 연관 Service 삭제 (claude-apps / platform / openwebui 내)."""
    if req.namespace not in _UNHEALTHY_NAMESPACES:
        raise HTTPException(status_code=400, detail=f"namespace not allowed: {req.namespace}")

    apps = client.AppsV1Api()
    core = client.CoreV1Api()
    deleted = {"deployment": False, "service": False}

    try:
        apps.delete_namespaced_deployment(name=req.deployment, namespace=req.namespace)
        deleted["deployment"] = True
    except client.ApiException as e:
        if e.status != 404:
            raise HTTPException(status_code=500, detail=f"deployment delete failed: {e.reason}")

    try:
        core.delete_namespaced_service(name=req.deployment, namespace=req.namespace)
        deleted["service"] = True
    except client.ApiException as e:
        if e.status != 404:
            logger.warning(f"service delete failed: {e.reason}")

    logger.info(f"admin deleted deployment {req.namespace}/{req.deployment}: {deleted}")
    return {"namespace": req.namespace, "deployment": req.deployment, **deleted}


# ==================== Admin App Management ====================

class AdminAppInfo(BaseModel):
    id: int
    owner_username: str
    owner_name: str | None = None
    app_name: str
    app_url: str
    pod_name: str | None = None
    status: str
    version: str
    visibility: str
    app_port: int
    pod_status: str | None = None
    pod_ip: str | None = None
    node_name: str | None = None
    restarts: int = 0
    view_count: int = 0
    unique_viewers: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class AdminAppsResponse(BaseModel):
    apps: list[AdminAppInfo]
    total: int
    collected_at: str


@router.get("/apps/list", response_model=AdminAppsResponse)
async def admin_list_apps(
    status_filter: str | None = None,
    _admin: dict = Depends(_require_admin),
):
    """관리자용 전체 배포 앱 목록 (K8s 실시간 상태 포함)."""
    from app.core.database import SessionLocal
    from app.models.app import DeployedApp as AppModel
    from app.models.user import User

    db = SessionLocal()
    try:
        q = db.query(AppModel, User).outerjoin(User, AppModel.owner_username == User.username)
        if status_filter:
            q = q.filter(AppModel.status == status_filter)
        rows = q.order_by(AppModel.created_at.desc()).all()
    finally:
        db.close()

    # K8s 실시간 Pod 상태 수집
    v1 = client.CoreV1Api()
    pod_map: dict[str, dict] = {}
    try:
        pods = v1.list_namespaced_pod(namespace="claude-apps")
        for pod in pods.items:
            labels = pod.metadata.labels or {}
            key = f"{labels.get('owner','')}/{labels.get('app-name','')}"
            statuses = pod.status.container_statuses or []
            restarts = sum(s.restart_count for s in statuses)
            waiting = None
            for s in statuses:
                w = getattr(s.state, "waiting", None)
                if w:
                    waiting = w.reason
                    break
            pod_map[key] = {
                "pod_status": waiting or pod.status.phase or "Unknown",
                "pod_ip": pod.status.pod_ip,
                "node_name": pod.spec.node_name,
                "restarts": restarts,
            }
    except Exception as e:
        logger.warning(f"K8s pod status fetch failed: {e}")

    now = datetime.now(timezone.utc)
    apps_out = []
    for app, user in rows:
        key = f"{app.owner_username.lower()}/{app.app_name.lower()}"
        k8s = pod_map.get(key, {})
        apps_out.append(AdminAppInfo(
            id=app.id,
            owner_username=app.owner_username,
            owner_name=user.name if user else None,
            app_name=app.app_name,
            app_url=app.app_url,
            pod_name=app.pod_name,
            status=app.status,
            version=app.version,
            visibility=app.visibility,
            app_port=app.app_port,
            pod_status=k8s.get("pod_status"),
            pod_ip=k8s.get("pod_ip"),
            node_name=k8s.get("node_name"),
            restarts=k8s.get("restarts", 0),
            view_count=getattr(app, "view_count", 0) or 0,
            unique_viewers=getattr(app, "unique_viewers", 0) or 0,
            created_at=app.created_at.isoformat() if app.created_at else None,
            updated_at=app.updated_at.isoformat() if app.updated_at else None,
        ))

    return AdminAppsResponse(apps=apps_out, total=len(apps_out), collected_at=now.isoformat())


class AdminAppActionRequest(BaseModel):
    owner_username: str
    app_name: str


@router.post("/apps/stop")
async def admin_stop_app(
    req: AdminAppActionRequest,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """관리자: 앱 중지 (replicas=0). DB status는 유지."""
    apps_v1 = client.AppsV1Api()
    from app.core.database import SessionLocal
    from app.models.app import DeployedApp as AppModel

    db = SessionLocal()
    try:
        app = db.query(AppModel).filter(
            AppModel.owner_username == req.owner_username,
            AppModel.app_name == req.app_name,
        ).first()
        if not app or not app.pod_name:
            raise HTTPException(status_code=404, detail="앱을 찾을 수 없습니다")
        pod_name = app.pod_name
        app.status = "stopped"
        app.updated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()

    try:
        apps_v1.patch_namespaced_deployment_scale(
            name=pod_name,
            namespace="claude-apps",
            body={"spec": {"replicas": 0}},
        )
    except client.ApiException as e:
        if e.status != 404:
            raise HTTPException(status_code=500, detail=f"스케일 다운 실패: {e.reason}")

    logger.info(f"admin stopped app {req.owner_username}/{req.app_name}")
    return {"stopped": True, "app_name": req.app_name, "owner": req.owner_username}


@router.post("/apps/start")
async def admin_start_app(
    req: AdminAppActionRequest,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """관리자: 중지된 앱 재시작 (replicas=1)."""
    apps_v1 = client.AppsV1Api()
    from app.core.database import SessionLocal
    from app.models.app import DeployedApp as AppModel

    db = SessionLocal()
    try:
        app = db.query(AppModel).filter(
            AppModel.owner_username == req.owner_username,
            AppModel.app_name == req.app_name,
        ).first()
        if not app or not app.pod_name:
            raise HTTPException(status_code=404, detail="앱을 찾을 수 없습니다")
        pod_name = app.pod_name
        app.status = "running"
        app.updated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()

    try:
        apps_v1.patch_namespaced_deployment_scale(
            name=pod_name,
            namespace="claude-apps",
            body={"spec": {"replicas": 1}},
        )
    except client.ApiException as e:
        if e.status != 404:
            raise HTTPException(status_code=500, detail=f"스케일 업 실패: {e.reason}")

    logger.info(f"admin started app {req.owner_username}/{req.app_name}")
    return {"started": True, "app_name": req.app_name, "owner": req.owner_username}


@router.post("/apps/reapprove")
async def admin_reapprove_app(
    req: AdminAppActionRequest,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """관리자: 회수된 앱을 재승인 대기 상태로 전환.

    suspended → pending_approval: 기존 승인 페이지(/apps/pending)에서 관리자가
    앱 내용을 검토한 뒤 명시적으로 승인해야만 서비스가 재개됩니다.
    Pod와 Ingress는 승인 시점에 재생성됩니다.
    """
    from app.core.database import SessionLocal
    from app.models.app import DeployedApp as AppModel

    db = SessionLocal()
    try:
        app = db.query(AppModel).filter(
            AppModel.owner_username == req.owner_username,
            AppModel.app_name == req.app_name,
        ).first()
        if not app:
            raise HTTPException(status_code=404, detail="앱을 찾을 수 없습니다")
        if app.status != "suspended":
            raise HTTPException(status_code=400, detail=f"회수된 앱만 재승인 요청 가능합니다 (현재: {app.status})")
        app.status = "pending_approval"
        app.approved_by = None
        app.approved_at = None
        app.updated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()

    logger.info(f"admin re-queued app {req.owner_username}/{req.app_name} for approval review")
    return {"reapprove_queued": True, "app_name": req.app_name, "owner": req.owner_username}


@router.post("/apps/recall")
async def admin_recall_app(
    req: AdminAppActionRequest,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """관리자: 배포 회수 — 갤러리 제거 + 외부 접근 차단 + Pod 종료.

    사용자의 코드/데이터(EFS)는 보존됩니다.
    사용자는 본인 터미널에서 앱을 다시 배포할 수 있습니다.
    완전 삭제(K8s 리소스 + DB)는 관리자 권한으로도 허용하지 않습니다.
    """
    from app.core.database import SessionLocal
    from app.models.app import DeployedApp as AppModel
    from kubernetes.client.exceptions import ApiException

    apps_v1 = client.AppsV1Api()
    net_v1 = client.NetworkingV1Api()
    core_v1 = client.CoreV1Api()

    db = SessionLocal()
    try:
        app = db.query(AppModel).filter(
            AppModel.owner_username == req.owner_username,
            AppModel.app_name == req.app_name,
        ).first()
        if not app:
            raise HTTPException(status_code=404, detail="앱을 찾을 수 없습니다")
        if app.status == "suspended":
            raise HTTPException(status_code=400, detail="이미 회수된 앱입니다")
        pod_name = app.pod_name

        # DB: 상태를 suspended, 비공개로 전환 (코드/데이터는 건드리지 않음)
        app.status = "suspended"
        app.visibility = "private"
        app.updated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()

    if pod_name:
        # Pod 종료 (replicas=0)
        try:
            apps_v1.patch_namespaced_deployment_scale(
                name=pod_name, namespace="claude-apps",
                body={"spec": {"replicas": 0}},
            )
        except ApiException as e:
            if e.status != 404:
                logger.warning(f"scale-down failed for recalled app {pod_name}: {e.reason}")

        # Ingress 삭제 — 외부 경로 차단
        try:
            net_v1.delete_namespaced_ingress(name=pod_name, namespace="claude-apps")
        except ApiException as e:
            if e.status != 404:
                logger.warning(f"ingress delete failed for recalled app {pod_name}: {e.reason}")

    logger.info(f"admin recalled app {req.owner_username}/{req.app_name} — K8s scaled-0, ingress removed, DB=suspended/private")
    return {"recalled": True, "app_name": req.app_name, "owner": req.owner_username}


# ==================== Maintenance Mode ====================

class MaintenanceRequest(BaseModel):
    is_active: bool
    title: str = "서비스 점검 중"
    description: str = ""
    start_time: str | None = None   # ISO 8601
    end_time: str | None = None     # ISO 8601


class MaintenanceResponse(BaseModel):
    is_active: bool
    title: str
    description: str
    start_time: str | None = None
    end_time: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None


def _get_or_create_maintenance(db) -> "MaintenanceModel":
    from app.models.maintenance import MaintenanceMode as MaintenanceModel
    row = db.query(MaintenanceModel).filter(MaintenanceModel.id == 1).first()
    if not row:
        row = MaintenanceModel(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("/maintenance", response_model=MaintenanceResponse)
async def get_maintenance(
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """점검 모드 현재 상태 조회."""
    row = _get_or_create_maintenance(db)
    return MaintenanceResponse(
        is_active=row.is_active,
        title=row.title,
        description=row.description,
        start_time=row.start_time.isoformat() if row.start_time else None,
        end_time=row.end_time.isoformat() if row.end_time else None,
        updated_by=row.updated_by,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.post("/maintenance", response_model=MaintenanceResponse)
async def set_maintenance(
    req: MaintenanceRequest,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """점검 모드 설정/해제."""
    from datetime import datetime, timezone
    row = _get_or_create_maintenance(db)
    row.is_active = req.is_active
    row.title = req.title.strip() or "서비스 점검 중"
    row.description = req.description.strip()
    row.updated_by = admin["sub"]
    row.updated_at = datetime.now(timezone.utc)

    if req.start_time:
        try:
            row.start_time = datetime.fromisoformat(req.start_time)
        except ValueError:
            row.start_time = None
    else:
        row.start_time = None

    if req.end_time:
        try:
            row.end_time = datetime.fromisoformat(req.end_time)
        except ValueError:
            row.end_time = None
    else:
        row.end_time = None

    db.commit()
    db.refresh(row)

    # Redis 캐시 무효화 (미들웨어가 다음 요청에서 DB 재조회)
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        if r:
            r.delete("maintenance:active")
    except Exception:
        pass

    action = "점검 모드 활성화" if req.is_active else "점검 모드 해제"
    logger.info(f"admin {admin['sub']}: {action} — {req.title}")

    return MaintenanceResponse(
        is_active=row.is_active,
        title=row.title,
        description=row.description,
        start_time=row.start_time.isoformat() if row.start_time else None,
        end_time=row.end_time.isoformat() if row.end_time else None,
        updated_by=row.updated_by,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


# ==================== Audit Logs ====================

@router.get("/audit-logs")
async def get_audit_logs(
    actor: str = None,
    action: str = None,
    days: int = 7,
    limit: int = 100,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """감사 로그 조회 (관리자용)."""
    from app.core.database import SessionLocal
    from app.models.audit_log import AuditLog
    from datetime import timedelta

    db = SessionLocal()
    query = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    if actor:
        query = query.filter(AuditLog.actor == actor)
    if action:
        query = query.filter(AuditLog.action == action)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = query.filter(AuditLog.timestamp >= cutoff)
    logs = query.limit(limit).all()
    db.close()

    return {
        "total": len(logs),
        "logs": [
            {
                "id": log.id,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "actor": log.actor,
                "action": log.action,
                "target": log.target,
                "detail": log.detail,
                "ip_address": log.ip_address,
            }
            for log in logs
        ],
    }


# ==================== Token Usage Daily Tracking ====================

def do_snapshot(db, settings: Settings) -> dict:
    """스냅샷 공통 로직 — API 엔드포인트와 백그라운드 스케줄러 모두에서 사용.

    실행 중인 Pod의 토큰 사용량을 수집하여 token_usage_daily + token_usage_hourly에 upsert.
    """
    from app.models.user import User
    from app.models.token_usage import TokenUsageDaily, TokenUsageHourly
    from app.models.session import TerminalSession

    v1 = client.CoreV1Api()
    namespace = settings.k8s_namespace
    pods = v1.list_namespaced_pod(
        namespace=namespace, label_selector="app=claude-terminal",
        field_selector="status.phase=Running",
    )

    users_db = {u.username: u.name for u in db.query(User).all()}
    now = datetime.now(timezone.utc)
    today = now.date()  # UTC date — must match UTC-based hour/slot below
    current_hour = now.hour
    current_slot = now.hour * 6 + now.minute // 10  # 0-143 (10-min resolution)

    saved = 0
    for pod in pods.items:
        pod_name = pod.metadata.name
        username = pod_name.replace("claude-terminal-", "").upper()
        input_t, output_t = _collect_tokens_from_pod(v1, pod_name, namespace)
        total = input_t + output_t
        # T20 활성화 전 legacy snapshot 경로 — 모델 정보 없어 Sonnet 가격으로 추정
        cost_usd = round(
            (input_t * _LEGACY_PRICE["input"]
             + output_t * _LEGACY_PRICE["output"]) / 1_000_000,
            6,
        )
        cost_krw = round(float(cost_usd) * _pricing.KRW_RATE)

        # 세션 시간 계산
        session = db.query(TerminalSession).filter(
            TerminalSession.pod_name == pod_name,
            TerminalSession.pod_status == "running",
        ).first()
        minutes = 0
        if session and session.started_at:
            delta = now - session.started_at
            minutes = int(delta.total_seconds() / 60)

        # ---- token_usage_daily upsert ----
        existing_daily = db.query(TokenUsageDaily).filter(
            TokenUsageDaily.username == username,
            TokenUsageDaily.usage_date == today,
        ).first()

        if existing_daily:
            existing_daily.input_tokens = input_t
            existing_daily.output_tokens = output_t
            existing_daily.total_tokens = total
            existing_daily.cost_usd = cost_usd
            existing_daily.cost_krw = cost_krw
            existing_daily.session_minutes = minutes
            existing_daily.last_activity_at = now
            existing_daily.user_name = users_db.get(username)
        else:
            record = TokenUsageDaily(
                username=username, user_name=users_db.get(username),
                usage_date=today, input_tokens=input_t, output_tokens=output_t,
                total_tokens=total, cost_usd=cost_usd, cost_krw=cost_krw,
                session_minutes=minutes, last_activity_at=now,
            )
            db.add(record)

        # ---- token_usage_hourly upsert (5-min slot) ----
        existing_hourly = db.query(TokenUsageHourly).filter(
            TokenUsageHourly.username == username,
            TokenUsageHourly.usage_date == today,
            TokenUsageHourly.slot == current_slot,
        ).first()

        if existing_hourly:
            existing_hourly.input_tokens = input_t
            existing_hourly.output_tokens = output_t
            existing_hourly.total_tokens = total
            existing_hourly.cost_usd = cost_usd
            existing_hourly.cost_krw = cost_krw
        else:
            hourly_record = TokenUsageHourly(
                username=username,
                usage_date=today, hour=current_hour, slot=current_slot,
                input_tokens=input_t, output_tokens=output_t,
                total_tokens=total, cost_usd=cost_usd, cost_krw=cost_krw,
            )
            db.add(hourly_record)

        saved += 1

    db.commit()
    return {"saved": saved, "date": str(today)}


@router.post("/token-usage/snapshot")
async def take_token_snapshot(
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """현재 실행 중인 Pod의 토큰 사용량을 DB에 스냅샷 저장."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        result = do_snapshot(db, settings)
    finally:
        db.close()
    return result


@router.get("/token-usage/hourly")
async def get_token_usage_hourly(
    date: str = None,
    admin: dict = Depends(_require_admin),
):
    """10분 단위 토큰 사용량 (스파크라인 차트용). 날짜 미지정 시 오늘.

    반환: users = { username: [144 slots] } (10분 × 144 = 24시간)
    """
    from app.core.database import SessionLocal
    from app.models.token_usage import TokenUsageHourly

    target = date_type.fromisoformat(date) if date else datetime.now(timezone.utc).date()
    db = SessionLocal()
    records = db.query(TokenUsageHourly).filter(
        TokenUsageHourly.usage_date == target,
    ).all()
    db.close()

    # 사용자별 144-slot 배열 구성 (10분 단위)
    users: dict[str, list[int]] = {}
    for r in records:
        if r.username not in users:
            users[r.username] = [0] * 144
        slot = r.slot if r.slot is not None else (r.hour * 6)
        if 0 <= slot < 144:
            users[r.username][slot] = r.total_tokens

    return {
        "date": str(target),
        "users": users,
        "resolution": "10min",
        "slots": 144,
    }


@router.get("/token-usage/daily")
async def get_daily_usage(
    date: str = None,  # YYYY-MM-DD, default today
    _admin: dict = Depends(_require_admin),
):
    """일별 토큰 사용량 조회 — 승인된 전체 사용자 포함 (데이터 없으면 0)."""
    from app.core.database import SessionLocal
    from app.models.token_usage import TokenUsageDaily
    from app.models.user import User

    target = date_type.fromisoformat(date) if date else datetime.now(timezone.utc).date()
    db = SessionLocal()

    # 승인된 전체 사용자
    all_users = db.query(User).filter(User.is_approved == True).all()

    # 해당 날짜 사용량 데이터
    records = db.query(TokenUsageDaily).filter(
        TokenUsageDaily.usage_date == target,
    ).all()
    usage_map = {r.username: r for r in records}
    db.close()

    # 전체 사용자 + 사용량 (없으면 0)
    user_list = []
    for user in all_users:
        usage = usage_map.get(user.username)
        user_list.append({
            "username": user.username,
            "user_name": usage.user_name if usage else (user.name or user.username),
            "input_tokens": usage.input_tokens if usage else 0,
            "output_tokens": usage.output_tokens if usage else 0,
            "total_tokens": usage.total_tokens if usage else 0,
            "cost_usd": float(usage.cost_usd) if usage else 0.0,
            "cost_krw": usage.cost_krw if usage else 0,
            "session_minutes": usage.session_minutes if usage else 0,
            "last_activity_at": usage.last_activity_at.isoformat() if usage and usage.last_activity_at else None,
        })

    # total_tokens 내림차순 정렬
    user_list.sort(key=lambda x: x["total_tokens"], reverse=True)

    # 주의: 각 사용자의 값은 세션 시작 이후 "누적" 토큰이다.
    # 전사 합계는 각 사용자의 누적값을 합산한 것이므로 "전사 누적 합계"이다.
    # 일간 증분 합계가 필요하면 전일 데이터와 비교해야 한다.
    return {
        "date": str(target),
        "users": user_list,
        "total_input": sum(u["input_tokens"] for u in user_list),
        "total_output": sum(u["output_tokens"] for u in user_list),
        "total_tokens": sum(u["total_tokens"] for u in user_list),
        "total_cost_usd": round(sum(u["cost_usd"] for u in user_list), 4),
        "total_cost_krw": sum(u["cost_krw"] for u in user_list),
        "note": "각 사용자 값은 세션 시작 이후 누적 토큰. 일간 증분은 전일 대비 차이로 계산 필요.",
    }


@router.get("/token-usage/monthly")
async def get_monthly_usage(
    month: str = None,  # YYYY-MM, default current
    _admin: dict = Depends(_require_admin),
):
    """월별 토큰 사용량 합계 — 승인된 전체 사용자 포함 (데이터 없으면 0).

    주의: token_usage_daily는 누적 스냅샷을 저장한다 (Pod의 jsonl에서 읽은 전체 누적값).
    월별 사용량 = 해당 월 마지막 스냅샷 - 해당 월 첫 스냅샷 (또는 전월 마지막 스냅샷).
    SUM은 누적값을 중복 합산하므로 사용하면 안 된다.
    """
    from app.core.database import SessionLocal
    from app.models.token_usage import TokenUsageDaily
    from app.models.user import User
    from sqlalchemy import func, extract

    if month:
        year, mon = month.split("-")
    else:
        today = datetime.now(timezone.utc).date()
        year, mon = today.year, today.month

    db = SessionLocal()

    # 승인된 전체 사용자
    all_users = db.query(User).filter(User.is_approved == True).all()

    # 해당 월 사용량: MAX(누적) - MIN(누적) = 해당 월 증분
    # session_minutes는 마지막 값이 가장 정확 (누적이므로 MAX)
    records = db.query(
        TokenUsageDaily.username,
        TokenUsageDaily.user_name,
        (func.max(TokenUsageDaily.input_tokens) - func.min(TokenUsageDaily.input_tokens)).label("input_tokens"),
        (func.max(TokenUsageDaily.output_tokens) - func.min(TokenUsageDaily.output_tokens)).label("output_tokens"),
        (func.max(TokenUsageDaily.total_tokens) - func.min(TokenUsageDaily.total_tokens)).label("total_tokens"),
        (func.max(TokenUsageDaily.cost_usd) - func.min(TokenUsageDaily.cost_usd)).label("cost_usd"),
        (func.max(TokenUsageDaily.cost_krw) - func.min(TokenUsageDaily.cost_krw)).label("cost_krw"),
        func.max(TokenUsageDaily.session_minutes).label("session_minutes"),
        func.max(TokenUsageDaily.last_activity_at).label("last_activity_at"),
    ).filter(
        extract("year", TokenUsageDaily.usage_date) == int(year),
        extract("month", TokenUsageDaily.usage_date) == int(mon),
    ).group_by(TokenUsageDaily.username, TokenUsageDaily.user_name).all()
    usage_map = {r.username: r for r in records}
    db.close()

    # 전체 사용자 + 사용량 (없으면 0)
    user_list = []
    for user in all_users:
        usage = usage_map.get(user.username)
        user_list.append({
            "username": user.username,
            "user_name": usage.user_name if usage else (user.name or user.username),
            "input_tokens": int(usage.input_tokens or 0) if usage else 0,
            "output_tokens": int(usage.output_tokens or 0) if usage else 0,
            "total_tokens": int(usage.total_tokens or 0) if usage else 0,
            "cost_usd": round(float(usage.cost_usd or 0), 4) if usage else 0.0,
            "cost_krw": int(usage.cost_krw or 0) if usage else 0,
            "session_minutes": int(usage.session_minutes or 0) if usage else 0,
            "last_activity_at": usage.last_activity_at.isoformat() if usage and usage.last_activity_at else None,
        })

    # total_tokens 내림차순 정렬
    user_list.sort(key=lambda x: x["total_tokens"], reverse=True)

    return {
        "month": f"{year}-{str(mon).zfill(2)}",
        "users": user_list,
    }


@router.get("/token-usage/daily-trend")
async def get_daily_trend(
    days: int = 30,
    _admin: dict = Depends(_require_admin),
):
    """전사 일별 토큰 사용량 추이 (최근 N일).

    주의: token_usage_daily는 누적 스냅샷을 저장한다.
    각 날짜의 사용자별 값은 그 날까지의 누적 토큰이므로, SUM하면 중복 합산된다.
    올바른 집계: 각 사용자의 해당일 값(MAX)을 그대로 합산 (일별 레코드는 사용자당 1행).
    일간 증분이 필요하면 클라이언트에서 전일 대비 차이를 계산한다.
    """
    from app.core.database import SessionLocal
    from app.models.token_usage import TokenUsageDaily
    from sqlalchemy import func

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    db = SessionLocal()
    # 각 날짜별 사용자 수 + 사용자별 누적값의 합 (일별 레코드는 사용자당 1행이므로 SUM = 전사 합계)
    # 단, 이 합계는 "각 사용자의 세션 시작 이후 누적"의 합이므로, 일간 증분이 아님.
    # 일간 증분을 구하려면 전일과의 차이를 계산해야 한다.
    records = db.query(
        TokenUsageDaily.usage_date,
        func.max(TokenUsageDaily.input_tokens).label("input_tokens"),
        func.max(TokenUsageDaily.output_tokens).label("output_tokens"),
        func.max(TokenUsageDaily.total_tokens).label("total_tokens"),
        func.max(TokenUsageDaily.cost_usd).label("cost_usd"),
        func.max(TokenUsageDaily.cost_krw).label("cost_krw"),
        func.count(func.distinct(TokenUsageDaily.username)).label("active_users"),
    ).filter(
        TokenUsageDaily.usage_date >= cutoff,
    ).group_by(TokenUsageDaily.usage_date).order_by(TokenUsageDaily.usage_date).all()
    db.close()

    return {
        "days": days,
        "trend": [{
            "date": str(r.usage_date),
            "input_tokens": int(r.input_tokens or 0),
            "output_tokens": int(r.output_tokens or 0),
            "total_tokens": int(r.total_tokens or 0),
            "cost_usd": round(float(r.cost_usd or 0), 4),
            "cost_krw": int(r.cost_krw or 0),
            "active_users": r.active_users,
        } for r in records],
    }


@router.get("/token-usage/monthly-trend")
async def get_monthly_trend(
    from_month: str = "2026-03",
    _admin: dict = Depends(_require_admin),
):
    """전사 월별 토큰 사용량 추이.

    주의: token_usage_daily는 누적 스냅샷. 월별 집계는 MAX - MIN으로 증분 계산.
    """
    from app.core.database import SessionLocal
    from app.models.token_usage import TokenUsageDaily
    from sqlalchemy import func, extract, cast, String

    from_date = date_type.fromisoformat(from_month + "-01")
    db = SessionLocal()
    records = db.query(
        func.to_char(TokenUsageDaily.usage_date, 'YYYY-MM').label("month"),
        (func.max(TokenUsageDaily.input_tokens) - func.min(TokenUsageDaily.input_tokens)).label("input_tokens"),
        (func.max(TokenUsageDaily.output_tokens) - func.min(TokenUsageDaily.output_tokens)).label("output_tokens"),
        (func.max(TokenUsageDaily.total_tokens) - func.min(TokenUsageDaily.total_tokens)).label("total_tokens"),
        (func.max(TokenUsageDaily.cost_usd) - func.min(TokenUsageDaily.cost_usd)).label("cost_usd"),
        (func.max(TokenUsageDaily.cost_krw) - func.min(TokenUsageDaily.cost_krw)).label("cost_krw"),
        func.count(func.distinct(TokenUsageDaily.username)).label("active_users"),
    ).filter(
        TokenUsageDaily.usage_date >= from_date,
    ).group_by(
        func.to_char(TokenUsageDaily.usage_date, 'YYYY-MM')
    ).order_by(
        func.to_char(TokenUsageDaily.usage_date, 'YYYY-MM')
    ).all()
    db.close()

    return {
        "from_month": from_month,
        "trend": [{
            "month": r.month,
            "input_tokens": int(r.input_tokens or 0),
            "output_tokens": int(r.output_tokens or 0),
            "total_tokens": int(r.total_tokens or 0),
            "cost_usd": round(float(r.cost_usd or 0), 4),
            "cost_krw": int(r.cost_krw or 0),
            "active_users": r.active_users,
        } for r in records],
    }


@router.get("/token-usage/model-breakdown")
async def get_model_breakdown(
    days: int = 30,
    _admin: dict = Depends(_require_admin),
):
    """모델별 토큰 사용량 분류 — Haiku 비율 리포트.

    T20 proxy 활성화(2026-04-12) 이후 쌓인 per-model_id 데이터를 집계.
    legacy-aggregate 행은 모델 불명이므로 별도 분류.
    """
    from app.core.database import SessionLocal
    from app.models.token_usage import TokenUsageEvent
    from sqlalchemy import func

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()

    # token_usage_event는 T20 proxy 경유 이벤트만 기록 (정확한 model_id 보유)
    records = db.query(
        TokenUsageEvent.model_id,
        func.count().label("event_count"),
        func.sum(TokenUsageEvent.input_tokens).label("input_tokens"),
        func.sum(TokenUsageEvent.output_tokens).label("output_tokens"),
        func.sum(TokenUsageEvent.cache_creation_input_tokens).label("cache_creation_tokens"),
        func.sum(TokenUsageEvent.cache_read_input_tokens).label("cache_read_tokens"),
        func.sum(TokenUsageEvent.cost_usd).label("cost_usd"),
        func.count(func.distinct(TokenUsageEvent.username)).label("user_count"),
    ).filter(
        TokenUsageEvent.recorded_at >= cutoff,
    ).group_by(TokenUsageEvent.model_id).all()
    db.close()

    _DISPLAY = {
        "claude-sonnet-4-6": "Claude Sonnet 4.6",
        "claude-haiku-4-5": "Claude Haiku 4.5",
        "claude-opus-4-6": "Claude Opus 4.6",
    }

    def _short(mid: str) -> str:
        lid = mid.lower()
        if "haiku" in lid:
            return "haiku"
        if "opus" in lid:
            return "opus"
        return "sonnet"

    breakdown = []
    total_tokens = 0
    total_cost = 0.0
    for r in records:
        t = int((r.input_tokens or 0) + (r.output_tokens or 0))
        c = float(r.cost_usd or 0)
        total_tokens += t
        total_cost += c
        breakdown.append({
            "model_id": r.model_id,
            "display_name": _DISPLAY.get(_short(r.model_id) and r.model_id, r.model_id),
            "model_key": _short(r.model_id),
            "event_count": r.event_count,
            "input_tokens": int(r.input_tokens or 0),
            "output_tokens": int(r.output_tokens or 0),
            "cache_creation_tokens": int(r.cache_creation_tokens or 0),
            "cache_read_tokens": int(r.cache_read_tokens or 0),
            "total_tokens": t,
            "cost_usd": round(c, 4),
            "cost_krw": round(c * _pricing.KRW_RATE),
            "user_count": r.user_count,
        })

    # token 기준 내림차순 정렬
    breakdown.sort(key=lambda x: x["total_tokens"], reverse=True)

    # 비율 계산
    for item in breakdown:
        item["token_pct"] = round(item["total_tokens"] / total_tokens * 100, 1) if total_tokens else 0.0
        item["cost_pct"] = round(item["cost_usd"] / total_cost * 100, 1) if total_cost else 0.0

    return {
        "days": days,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "total_cost_krw": round(total_cost * _pricing.KRW_RATE),
        "breakdown": breakdown,
        "source": "token_usage_event",
        "note": "T20 proxy 경유 이벤트만 집계. 2026-04-12 이전 legacy-aggregate 데이터 미포함.",
    }


@router.get("/token-usage/user/{username}")
async def get_user_usage_history(
    username: str,
    days: int = 30,
    _admin: dict = Depends(_require_admin),
):
    """사용자별 일별 사용량 이력."""
    from app.core.database import SessionLocal
    from app.models.token_usage import TokenUsageDaily

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    db = SessionLocal()
    records = db.query(TokenUsageDaily).filter(
        TokenUsageDaily.username == username.upper(),
        TokenUsageDaily.usage_date >= cutoff,
    ).order_by(TokenUsageDaily.usage_date.desc()).all()
    db.close()

    return {
        "username": username.upper(),
        "days": days,
        "history": [{
            "date": str(r.usage_date),
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "total_tokens": r.total_tokens, "cost_usd": float(r.cost_usd),
            "cost_krw": r.cost_krw, "session_minutes": r.session_minutes,
        } for r in records],
    }


# ==================== 토큰 할당 정책 ====================


@router.get("/token-quota/templates")
async def get_quota_templates(_admin=Depends(_require_admin)):
    """토큰 할당 정책 템플릿 목록."""
    from app.core.database import SessionLocal
    from app.models.token_quota import TokenQuotaTemplate

    db = SessionLocal()
    try:
        templates = db.query(TokenQuotaTemplate).order_by(TokenQuotaTemplate.id).all()
        return {
            "templates": [
                {
                    "id": t.id,
                    "name": t.name,
                    "description": t.description,
                    "cost_limit_usd": float(t.cost_limit_usd),
                    "refresh_cycle": t.refresh_cycle,
                    "is_unlimited": t.is_unlimited,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                }
                for t in templates
            ]
        }
    finally:
        db.close()


@router.post("/token-quota/templates")
async def create_quota_template(data: dict, _admin=Depends(_require_admin)):
    """토큰 할당 정책 템플릿 생성."""
    from app.core.database import SessionLocal
    from app.models.token_quota import TokenQuotaTemplate

    db = SessionLocal()
    try:
        # 중복 이름 확인
        existing = db.query(TokenQuotaTemplate).filter(TokenQuotaTemplate.name == data["name"]).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Template '{data['name']}' already exists")

        template = TokenQuotaTemplate(
            name=data["name"],
            description=data.get("description"),
            cost_limit_usd=data["cost_limit_usd"],
            refresh_cycle=data["refresh_cycle"],
            is_unlimited=data.get("is_unlimited", False),
        )
        db.add(template)
        db.commit()
        db.refresh(template)
        return {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "cost_limit_usd": float(template.cost_limit_usd),
            "refresh_cycle": template.refresh_cycle,
            "is_unlimited": template.is_unlimited,
        }
    finally:
        db.close()


@router.put("/token-quota/templates/{template_id}")
async def update_quota_template(template_id: int, data: dict, _admin=Depends(_require_admin)):
    """토큰 할당 정책 템플릿 수정."""
    from app.core.database import SessionLocal
    from app.models.token_quota import TokenQuotaTemplate

    db = SessionLocal()
    try:
        template = db.query(TokenQuotaTemplate).filter(TokenQuotaTemplate.id == template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        if "name" in data:
            template.name = data["name"]
        if "description" in data:
            template.description = data["description"]
        if "cost_limit_usd" in data:
            template.cost_limit_usd = data["cost_limit_usd"]
        if "refresh_cycle" in data:
            template.refresh_cycle = data["refresh_cycle"]
        if "is_unlimited" in data:
            template.is_unlimited = data["is_unlimited"]

        db.commit()
        db.refresh(template)
        return {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "cost_limit_usd": float(template.cost_limit_usd),
            "refresh_cycle": template.refresh_cycle,
            "is_unlimited": template.is_unlimited,
        }
    finally:
        db.close()


@router.delete("/token-quota/templates/{template_id}")
async def delete_quota_template(template_id: int, _admin=Depends(_require_admin)):
    """토큰 할당 정책 템플릿 삭제."""
    from app.core.database import SessionLocal
    from app.models.token_quota import TokenQuotaTemplate

    db = SessionLocal()
    try:
        template = db.query(TokenQuotaTemplate).filter(TokenQuotaTemplate.id == template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        db.delete(template)
        db.commit()
        return {"deleted": True, "id": template_id}
    finally:
        db.close()


@router.get("/token-quota/assignments")
async def get_quota_assignments(_admin=Depends(_require_admin)):
    """사용자별 토큰 할당 정책 조회."""
    from app.core.database import SessionLocal
    from app.models.token_quota import TokenQuotaAssignment
    from app.models.user import User

    db = SessionLocal()
    try:
        assignments = db.query(TokenQuotaAssignment).order_by(TokenQuotaAssignment.assigned_at.desc()).all()
        # 사용자 이름 매핑
        users_db = {u.username: u.name for u in db.query(User).all()}
        return {
            "assignments": [
                {
                    "id": a.id,
                    "user_id": a.user_id,
                    "username": a.username,
                    "user_name": users_db.get(a.username),
                    "template_name": a.template_name,
                    "cost_limit_usd": float(a.cost_limit_usd),
                    "refresh_cycle": a.refresh_cycle,
                    "is_unlimited": a.is_unlimited,
                    "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
                }
                for a in assignments
            ]
        }
    finally:
        db.close()


@router.post("/token-quota/assign")
async def assign_quota(data: dict, _admin=Depends(_require_admin)):
    """사용자에게 토큰 할당 정책 적용."""
    from app.core.database import SessionLocal
    from app.models.token_quota import TokenQuotaTemplate, TokenQuotaAssignment
    from app.models.user import User

    usernames = data.get("usernames", [])
    template_name = data.get("template_name")
    if not usernames or not template_name:
        raise HTTPException(status_code=400, detail="usernames and template_name required")

    db = SessionLocal()
    try:
        # 템플릿 조회
        template = db.query(TokenQuotaTemplate).filter(TokenQuotaTemplate.name == template_name).first()
        if not template:
            raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

        results = []
        for uname in usernames:
            uname_upper = uname.upper()
            user = db.query(User).filter(User.username == uname_upper).first()

            # 기존 할당이 있으면 업데이트, 없으면 생성
            existing = db.query(TokenQuotaAssignment).filter(
                TokenQuotaAssignment.username == uname_upper,
            ).first()

            if existing:
                existing.template_name = template.name
                existing.cost_limit_usd = template.cost_limit_usd
                existing.refresh_cycle = template.refresh_cycle
                existing.is_unlimited = template.is_unlimited
                existing.user_id = user.id if user else existing.user_id
                existing.assigned_at = datetime.now(timezone.utc)
                results.append({"username": uname_upper, "action": "updated"})
            else:
                assignment = TokenQuotaAssignment(
                    user_id=user.id if user else None,
                    username=uname_upper,
                    template_name=template.name,
                    cost_limit_usd=template.cost_limit_usd,
                    refresh_cycle=template.refresh_cycle,
                    is_unlimited=template.is_unlimited,
                )
                db.add(assignment)
                results.append({"username": uname_upper, "action": "created"})

        db.commit()
        return {"template_name": template_name, "results": results}
    finally:
        db.close()


def _check_user_quota(db, username: str) -> dict | None:
    """사용자 토큰 할당 잔여량 확인 — 내부 헬퍼.

    Returns dict with quota info, or None if no assignment exists.
    """
    from app.models.token_quota import TokenQuotaAssignment
    from app.models.token_usage import TokenUsageDaily
    from sqlalchemy import func

    assignment = db.query(TokenQuotaAssignment).filter(
        TokenQuotaAssignment.username == username.upper(),
    ).first()

    if not assignment:
        return None

    # 주기별 시작일 계산
    today = datetime.now(timezone.utc).date()
    refresh_cycle = assignment.refresh_cycle

    if refresh_cycle == "daily":
        cycle_start = today
    elif refresh_cycle == "weekly":
        cycle_start = today - timedelta(days=today.weekday())  # Monday
    elif refresh_cycle == "monthly":
        cycle_start = today.replace(day=1)
    else:
        cycle_start = today

    # 해당 주기 사용량 합산
    usage = db.query(
        func.coalesce(func.sum(TokenUsageDaily.cost_usd), 0)
    ).filter(
        TokenUsageDaily.username == username.upper(),
        TokenUsageDaily.usage_date >= cycle_start,
        TokenUsageDaily.usage_date <= today,
    ).scalar()

    current_usage = float(usage)
    cost_limit = float(assignment.cost_limit_usd)
    remaining = max(cost_limit - current_usage, 0.0)

    return {
        "username": username.upper(),
        "template_name": assignment.template_name,
        "cost_limit_usd": cost_limit,
        "current_usage_usd": round(current_usage, 4),
        "remaining_usd": round(remaining, 4),
        "is_exceeded": current_usage >= cost_limit,
        "is_unlimited": assignment.is_unlimited,
        "refresh_cycle": refresh_cycle,
        "cycle_start": str(cycle_start),
        "cycle_end": str(today),
    }


@router.get("/token-quota/check/{username}")
async def check_quota(username: str, _admin=Depends(_require_admin)):
    """사용자 토큰 할당 잔여량 확인."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        result = _check_user_quota(db, username)
        if not result:
            raise HTTPException(status_code=404, detail=f"No quota assignment for '{username}'")
        return result
    finally:
        db.close()


# ==================== 프롬프트 감사 ====================


@router.get("/prompt-audit/summary")
async def get_prompt_audit_summary(
    date_from: str = None,   # YYYY-MM-DD
    date_to: str = None,     # YYYY-MM-DD
    admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """프롬프트 카테고리별 사용 추이 요약."""
    from app.core.database import SessionLocal
    from app.services.prompt_audit_service import PromptAuditService

    parsed_from = None
    parsed_to = None
    if date_from:
        parsed_from = date_type.fromisoformat(date_from)
    if date_to:
        parsed_to = date_type.fromisoformat(date_to)

    db = SessionLocal()
    try:
        svc = PromptAuditService()
        return svc.get_summary(db, date_from=parsed_from, date_to=parsed_to)
    finally:
        db.close()


@router.get("/prompt-audit/flags")
async def get_prompt_audit_flags(
    severity: str = None,      # low, medium, high, critical
    reviewed: bool = None,
    limit: int = 50,
    admin: dict = Depends(_require_admin),
):
    """보안 위반 플래그 목록."""
    from app.core.database import SessionLocal
    from app.services.prompt_audit_service import PromptAuditService

    db = SessionLocal()
    try:
        svc = PromptAuditService()
        flags = svc.get_flags(db, severity=severity, reviewed=reviewed, limit=limit)
        return {"flags": flags}
    finally:
        db.close()


@router.post("/prompt-audit/flags/{flag_id}/review")
async def review_prompt_flag(
    flag_id: int,
    admin: dict = Depends(_require_admin),
):
    """보안 플래그 검토 완료 처리."""
    from app.core.database import SessionLocal
    from app.services.prompt_audit_service import PromptAuditService

    db = SessionLocal()
    try:
        result = PromptAuditService.review_flag(db, flag_id, reviewer=admin.get("sub", "admin"))
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    finally:
        db.close()


@router.post("/prompt-audit/collect")
async def trigger_prompt_audit(
    admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """수동 프롬프트 감사 트리거 — 즉시 수집·분석 실행."""
    from app.core.database import SessionLocal
    from app.services.prompt_audit_service import PromptAuditService

    db = SessionLocal()
    try:
        svc = PromptAuditService()
        result = svc.collect_and_analyze(db, namespace=settings.k8s_namespace)
        return result
    finally:
        db.close()


# ==================== Deployed Apps ====================


@router.get("/apps")
async def list_deployed_apps(
    admin: dict = Depends(_require_admin),
):
    """관리자용 — 전체 배포 앱 목록."""
    from app.core.database import SessionLocal
    from app.models.app import DeployedApp, AppACL
    from app.models.user import User
    from sqlalchemy import func

    db = SessionLocal()
    try:
        acl_count = (
            db.query(AppACL.app_id, func.count(AppACL.id).label("cnt"))
            .filter(AppACL.revoked_at.is_(None))
            .group_by(AppACL.app_id)
            .subquery()
        )

        apps = (
            db.query(DeployedApp, User.name, acl_count.c.cnt)
            .outerjoin(User, User.username == DeployedApp.owner_username)
            .outerjoin(acl_count, acl_count.c.app_id == DeployedApp.id)
            .filter(DeployedApp.status != "deleted")
            .order_by(DeployedApp.created_at.desc())
            .all()
        )

        return {
            "apps": [
                {
                    "id": app.id,
                    "owner_username": app.owner_username,
                    "owner_name": owner_name or app.owner_username,
                    "app_name": app.app_name,
                    "app_url": app.app_url,
                    "pod_name": app.pod_name,
                    "status": app.status,
                    "version": app.version,
                    "acl_count": cnt or 0,
                    "created_at": app.created_at.isoformat() if app.created_at else None,
                    "updated_at": app.updated_at.isoformat() if app.updated_at else None,
                }
                for app, owner_name, cnt in apps
            ]
        }
    finally:
        db.close()


# ==================== Storage Usage ====================


@router.get("/storage-usage")
async def get_storage_usage(
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """승인된 사용자별 스토리지 보존 정책 및 만료 상태를 반환한다.

    실제 디스크 사용량 측정은 kubectl exec이 필요해 느리므로,
    정책 설정값과 만료 계산만 반환한다.
    """
    from app.core.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        v1 = client.CoreV1Api()
        namespace = settings.k8s_namespace

        # 실행 중인 Pod 목록 (Pod 상태 판별용)
        running_pods: set[str] = set()
        try:
            pods = v1.list_namespaced_pod(
                namespace=namespace,
                label_selector="app=claude-terminal",
            )
            for pod in pods.items:
                username = pod.metadata.name.replace("claude-terminal-", "").upper()
                running_pods.add(username)
        except Exception as e:
            logger.warning(f"Pod 목록 조회 실패 (스토리지): {e}")

        # 승인된 사용자 전체 조회
        users = (
            db.query(User)
            .filter(User.is_approved == True)  # noqa: E712
            .order_by(User.username)
            .all()
        )

        now = datetime.now(timezone.utc)
        retention_map = {
            "7d": timedelta(days=7),
            "30d": timedelta(days=30),
            "90d": timedelta(days=90),
            "180d": timedelta(days=180),
        }

        result = []
        for user in users:
            # 만료 계산
            retention_td = retention_map.get(user.storage_retention)
            base_date = user.approved_at or user.created_at

            expires_at = None
            days_remaining = None
            status = "active"

            if retention_td and base_date:
                expires_at = base_date + retention_td
                days_remaining = round(
                    (expires_at - now).total_seconds() / 86400, 1
                )
                if days_remaining <= 0:
                    status = "expired"
                elif days_remaining <= 3:
                    status = "warning"

            if user.storage_retention == "unlimited":
                status = "unlimited"

            result.append({
                "username": user.username,
                "name": user.name,
                "storage_retention": user.storage_retention,
                "pod_status": "running" if user.username in running_pods else "stopped",
                "retention_status": status,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "days_remaining": days_remaining,
                "approved_at": user.approved_at.isoformat() if user.approved_at else None,
                "workspace_path": f"/home/node/workspace/users/{user.username.lower()}/",
            })

        # 요약 통계
        total = len(result)
        by_retention = {}
        by_status = {}
        for r in result:
            ret = r["storage_retention"]
            by_retention[ret] = by_retention.get(ret, 0) + 1
            st = r["retention_status"]
            by_status[st] = by_status.get(st, 0) + 1

        return {
            "users": result,
            "total": total,
            "summary": {
                "by_retention": by_retention,
                "by_status": by_status,
            },
            "collected_at": now.isoformat(),
        }
    finally:
        db.close()


# ==================== External API Proxy: Domain Whitelist ====================


class AllowedDomainCreate(BaseModel):
    domain: str
    description: str | None = None
    is_wildcard: bool = False


class AllowedDomainUpdate(BaseModel):
    enabled: bool | None = None
    description: str | None = None


class AllowedDomainResponse(BaseModel):
    id: int
    domain: str
    is_wildcard: bool
    description: str | None
    enabled: bool
    created_by: str | None
    created_at: str | None


@router.get("/allowed-domains")
async def list_allowed_domains(
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """허용된 외부 도메인 목록 조회."""
    from app.models.proxy import AllowedDomain

    domains = db.query(AllowedDomain).order_by(AllowedDomain.id).all()
    return {
        "domains": [
            {
                "id": d.id,
                "domain": d.domain,
                "is_wildcard": d.is_wildcard,
                "description": d.description,
                "enabled": d.enabled,
                "created_by": d.created_by,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in domains
        ]
    }


@router.post("/allowed-domains", status_code=201)
async def add_allowed_domain(
    body: AllowedDomainCreate,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """새 도메인을 화이트리스트에 추가."""
    from app.models.proxy import AllowedDomain

    # 중복 확인
    existing = db.query(AllowedDomain).filter(AllowedDomain.domain == body.domain).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Domain '{body.domain}' already exists")

    domain = AllowedDomain(
        domain=body.domain,
        is_wildcard=body.is_wildcard,
        description=body.description,
        enabled=True,
        created_by=admin.get("sub"),
    )
    db.add(domain)
    db.commit()
    db.refresh(domain)

    return {
        "id": domain.id,
        "domain": domain.domain,
        "is_wildcard": domain.is_wildcard,
        "description": domain.description,
        "enabled": domain.enabled,
        "created_by": domain.created_by,
        "created_at": domain.created_at.isoformat() if domain.created_at else None,
    }


@router.patch("/allowed-domains/{domain_id}")
async def update_allowed_domain(
    domain_id: int,
    body: AllowedDomainUpdate,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """도메인 활성/비활성 토글 또는 설명 업데이트."""
    from app.models.proxy import AllowedDomain

    domain = db.query(AllowedDomain).filter(AllowedDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")

    if body.enabled is not None:
        domain.enabled = body.enabled
    if body.description is not None:
        domain.description = body.description

    db.commit()
    db.refresh(domain)

    return {
        "id": domain.id,
        "domain": domain.domain,
        "is_wildcard": domain.is_wildcard,
        "description": domain.description,
        "enabled": domain.enabled,
        "created_by": domain.created_by,
        "created_at": domain.created_at.isoformat() if domain.created_at else None,
    }


@router.delete("/allowed-domains/{domain_id}")
async def delete_allowed_domain(
    domain_id: int,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """화이트리스트에서 도메인 삭제."""
    from app.models.proxy import AllowedDomain

    domain = db.query(AllowedDomain).filter(AllowedDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")

    db.delete(domain)
    db.commit()
    return {"deleted": True, "domain": domain.domain}


# ==================== External API Proxy: Access Logs ====================


@router.get("/proxy-logs")
async def get_proxy_logs(
    skip: int = 0,
    limit: int = 50,
    user_id: str | None = None,
    domain: str | None = None,
    admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """프록시 접근 로그 조회 (페이지네이션 + 필터)."""
    from app.models.proxy import ProxyAccessLog

    query = db.query(ProxyAccessLog).order_by(ProxyAccessLog.created_at.desc())

    if user_id:
        query = query.filter(ProxyAccessLog.user_id == user_id)
    if domain:
        query = query.filter(ProxyAccessLog.domain.ilike(f"%{domain}%"))

    total = query.count()
    logs = query.offset(skip).limit(limit).all()

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "logs": [
            {
                "id": log.id,
                "user_id": log.user_id,
                "domain": log.domain,
                "method": log.method,
                "allowed": log.allowed,
                "response_time_ms": log.response_time_ms,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }


# ==================== Admin Broadcast (MMS + WebSocket) ====================


class BroadcastRequest(BaseModel):
    message: str
    subject: str = "[Otto AI] 공지"
    targets: list[str] = []  # empty = all active users, or list of usernames
    channels: list[str] = ["mms"]  # "mms", "websocket", or both


class BroadcastResponse(BaseModel):
    mms_sent: int = 0
    mms_failed: int = 0
    ws_sent: int = 0
    targets: list[str] = []


@router.post("/broadcast", response_model=BroadcastResponse)
async def admin_broadcast(
    request: BroadcastRequest,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """관리자 공지 발송 (MMS + WebSocket).

    targets가 비어있으면 현재 활성 세션의 모든 사용자에게 발송.
    """
    from app.models.session import TerminalSession
    from app.models.user import User

    # Determine target users
    if request.targets:
        users = db.query(User).filter(User.username.in_([u.upper() for u in request.targets])).all()
    else:
        # All users with active sessions
        active_sessions = db.query(TerminalSession).filter(TerminalSession.pod_status == "running").all()
        active_usernames = [s.username for s in active_sessions]
        users = db.query(User).filter(User.username.in_(active_usernames)).all() if active_usernames else []

    result = BroadcastResponse(targets=[u.username for u in users])

    # MMS channel
    if "mms" in request.channels:
        sms_url = settings.sms_gateway_url
        sms_auth = settings.sms_auth_string
        if sms_url and sms_auth:
            pw_base64 = base64.b64encode(sms_auth.encode()).decode()
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as http:
                for user in users:
                    phone = user.phone_number
                    if not phone or phone == "None":
                        continue
                    # Normalize phone number
                    cleaned = re.sub(r"[\s\-\.]", "", phone)
                    if len(cleaned) == 11:
                        formatted = f"{cleaned[:3]}-{cleaned[3:7]}-{cleaned[7:]}"
                    else:
                        formatted = phone

                    payload = {
                        "TranType": "6",  # MMS (no character limit)
                        "TranPhone": formatted,
                        "TranCallBack": settings.sms_callback_number,
                        "TranMsg": f"{request.subject}\n\n{request.message}",
                        "SysPw": pw_base64,
                    }
                    try:
                        resp = await http.post(sms_url, json=payload)
                        data = resp.json()
                        if data.get("d", {}).get("Result", {}).get("ResultCode") == "1":
                            result.mms_sent += 1
                        else:
                            result.mms_failed += 1
                    except Exception:
                        result.mms_failed += 1

    # WebSocket channel — push message to Pod's ttyd terminals via /dev/pts
    if "websocket" in request.channels:
        v1 = client.CoreV1Api()
        namespace = settings.k8s_namespace
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        for user in users:
            pod_name = f"claude-terminal-{user.username.lower()}"
            try:
                # ANSI 컬러 스타일 터미널 공지
                lines = [
                    "",
                    "  \\033[1;33m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\033[0m",
                    f"  \\033[1;37;44m 📢 관리자 공지 \\033[0m \\033[1;36m{request.subject}\\033[0m",
                    f"  \\033[0;37m{request.message}\\033[0m",
                    f"  \\033[0;90m🕐 {now_str} UTC | Otto AI Platform\\033[0m",
                    "  \\033[1;33m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\033[0m",
                    "",
                ]
                msg = "\\n".join(lines)
                stream(
                    v1.connect_get_namespaced_pod_exec,
                    pod_name,
                    namespace,
                    container="terminal",
                    command=["bash", "-c", f'for pts in /dev/pts/[0-9]*; do echo -e "{msg}" > "$pts" 2>/dev/null; done'],
                    stderr=True,
                    stdout=True,
                )
                result.ws_sent += 1
            except Exception:
                pass

    logger.info(f"Admin broadcast by {_admin['sub']}: mms={result.mms_sent}, ws={result.ws_sent}, targets={len(users)}")
    return result


# ==================== App Approval Workflow ====================


class RejectRequest(BaseModel):
    reason: str


@router.get("/apps/pending")
async def list_pending_apps(
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """배포 승인 대기 앱 목록 (관리자 전용).

    status='pending_approval' + owner 사용자 정보 조인.
    """
    from app.models.app import DeployedApp
    from app.models.user import User

    apps = (
        db.query(DeployedApp)
        .filter(DeployedApp.status == "pending_approval")
        .order_by(DeployedApp.created_at.asc())
        .all()
    )

    # owner 정보 일괄 조회
    owner_usernames = list({a.owner_username for a in apps})
    users_by_uname = {}
    if owner_usernames:
        rows = db.query(User).filter(User.username.in_(owner_usernames)).all()
        users_by_uname = {u.username: u for u in rows}

    results = []
    for a in apps:
        u = users_by_uname.get(a.owner_username)
        results.append({
            "id": a.id,
            "owner_username": a.owner_username,
            "owner_name": u.name if u else None,
            "owner_team": getattr(u, "team_name", None) if u else None,
            "app_name": a.app_name,
            "app_url": a.app_url,
            "pod_name": a.pod_name,
            "version": a.version,
            "visibility": a.visibility,
            "app_port": a.app_port,
            "auth_mode": getattr(a, "auth_mode", "system") or "system",
            "custom_2fa_attested": bool(getattr(a, "custom_2fa_attested", False)),
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })
    return {"apps": results, "total": len(results)}


@router.post("/apps/{app_id}/approve")
async def approve_app(
    app_id: int,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """앱 배포 승인 (관리자 전용). pending_approval → running.

    auth_mode='custom'이면 Ingress 재생성 (auth annotation 제거).
    """
    from app.models.app import DeployedApp
    from app.models.user import User

    app = db.query(DeployedApp).filter(DeployedApp.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="앱을 찾을 수 없습니다")
    if app.status != "pending_approval":
        raise HTTPException(status_code=400, detail=f"승인 대기 상태가 아닙니다 (current={app.status})")

    admin_username = _admin["sub"]
    app.status = "running"
    app.approved_by = admin_username
    app.approved_at = datetime.now(timezone.utc)
    app.rejection_reason = None
    app.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(app)

    # custom 모드: Ingress 재생성 (auth annotation 제거 → 앱 자체 로그인이 전담)
    # 실패 시 DB 상태를 pending_approval로 롤백 (ingress 없이 running이면 도달 불가 + 승인 표기 불일치).
    if app.auth_mode == "custom" and app.pod_name:
        ingress_ok = False
        try:
            from app.services.app_deploy_service import AppDeployService
            from kubernetes.client.exceptions import ApiException

            deploy_svc = AppDeployService(settings)
            # 기존 ingress 삭제
            try:
                deploy_svc.networking.delete_namespaced_ingress(
                    name=app.pod_name,
                    namespace="claude-apps",
                )
            except ApiException as e:
                if e.status != 404:
                    logger.warning(f"approve_app: delete ingress failed ({app.pod_name}): {e}")

            # owner 사용자의 slug 조회
            owner = db.query(User).filter(User.username == app.owner_username).first()
            slug = owner.app_slug if owner else None
            if slug:
                deploy_svc._create_app_ingress(
                    app.pod_name, slug, app.app_name, app.app_port, auth_mode="custom"
                )
                ingress_ok = True
                logger.info(f"Ingress recreated for approved custom app {app.pod_name}")
            else:
                logger.error(f"approve_app: no slug for owner {app.owner_username}")
        except Exception as e:
            logger.error(f"approve_app: ingress recreate failed for {app.pod_name}: {e}")

        if not ingress_ok:
            # 롤백: 승인 취소하고 pending_approval 복귀. 관리자에게 500 알림.
            app.status = "pending_approval"
            app.approved_by = None
            app.approved_at = None
            app.updated_at = datetime.now(timezone.utc)
            db.commit()
            raise HTTPException(
                status_code=500,
                detail="Ingress 재생성 실패 — 승인이 롤백되었습니다. 앱 상태를 확인 후 재시도하세요.",
            )

    logger.info(
        f"App approved by {admin_username}: "
        f"{app.owner_username}/{app.app_name} (auth_mode={app.auth_mode})"
    )
    return {
        "approved": True,
        "app_id": app.id,
        "status": app.status,
        "approved_by": app.approved_by,
        "approved_at": app.approved_at.isoformat() if app.approved_at else None,
    }


@router.post("/apps/{app_id}/reject")
async def reject_app(
    app_id: int,
    request: RejectRequest,
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """앱 배포 거절 (관리자 전용). pending_approval → rejected.

    사유(reason) 10자 이상 필수. K8s 리소스는 삭제하여 리소스 낭비 방지.
    """
    reason = (request.reason or "").strip()
    if len(reason) < 10:
        raise HTTPException(status_code=400, detail="거절 사유는 10자 이상 입력해주세요")

    from app.models.app import DeployedApp

    app = db.query(DeployedApp).filter(DeployedApp.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="앱을 찾을 수 없습니다")
    if app.status != "pending_approval":
        raise HTTPException(status_code=400, detail=f"승인 대기 상태가 아닙니다 (current={app.status})")

    admin_username = _admin["sub"]
    app.status = "rejected"
    app.rejection_reason = reason[:500]
    app.updated_at = datetime.now(timezone.utc)
    db.commit()

    # K8s 리소스 삭제 (Pod/Service/Ingress)
    if app.pod_name:
        try:
            from app.services.app_deploy_service import AppDeployService
            deploy_svc = AppDeployService(settings)
            deploy_svc._delete_app_resources(app.pod_name)
            logger.info(f"K8s resources deleted for rejected app {app.pod_name}")
        except Exception as e:
            logger.error(f"reject_app: k8s delete failed for {app.pod_name}: {e}")

    logger.info(
        f"App rejected by {admin_username}: "
        f"{app.owner_username}/{app.app_name} — {reason[:80]}"
    )
    return {
        "rejected": True,
        "app_id": app.id,
        "status": app.status,
        "rejection_reason": app.rejection_reason,
    }


@router.post("/users/{username}/custom-auth-grant")
async def grant_custom_auth_permission(
    username: str,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """사용자에게 custom 로그인 배포 권한 부여 (관리자 전용)."""
    from app.models.user import User

    u = db.query(User).filter(User.username == username.upper()).first()
    if not u:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    u.can_deploy_custom_auth = True
    db.commit()
    logger.info(f"custom_auth granted to {username} by {_admin['sub']}")
    return {"username": u.username, "can_deploy_custom_auth": True}


@router.post("/users/{username}/custom-auth-revoke")
async def revoke_custom_auth_permission(
    username: str,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """사용자의 custom 로그인 배포 권한 회수 (관리자 전용)."""
    from app.models.user import User

    u = db.query(User).filter(User.username == username.upper()).first()
    if not u:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    u.can_deploy_custom_auth = False
    db.commit()
    logger.info(f"custom_auth revoked from {username} by {_admin['sub']}")
    return {"username": u.username, "can_deploy_custom_auth": False}


_VALID_MODEL_TIERS = {"sonnet", "haiku", "auto"}


@router.patch("/users/{username}/model-tier")
async def set_user_model_tier(
    username: str,
    tier: str,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """사용자 모델 티어 설정 (관리자 전용).

    tier 값:
      sonnet — 클라이언트 요청 모델 그대로 사용 (기본)
      haiku  — Haiku로 강제 다운그레이드 (비용 절감)
      auto   — 향후 확장 예약 (현재는 sonnet과 동일)
    """
    from app.models.user import User

    if tier not in _VALID_MODEL_TIERS:
        raise HTTPException(
            status_code=422,
            detail=f"유효하지 않은 tier: '{tier}'. 허용값: {sorted(_VALID_MODEL_TIERS)}",
        )
    u = db.query(User).filter(User.username == username.upper()).first()
    if not u:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    u.model_tier = tier
    db.commit()
    logger.info(f"model_tier set to '{tier}' for {username} by {_admin['sub']}")
    return {"username": u.username, "model_tier": u.model_tier}
