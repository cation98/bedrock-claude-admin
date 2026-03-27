"""Admin-only API: token usage analytics + infrastructure status."""
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from kubernetes import client
from kubernetes.stream import stream
from pydantic import BaseModel

from app.core.config import Settings, get_settings
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


# Bedrock Claude Sonnet pricing
INPUT_PRICE = 3.0 / 1_000_000
OUTPUT_PRICE = 15.0 / 1_000_000
KRW_RATE = 1380


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
        cost_usd = round(input_tokens * INPUT_PRICE + output_tokens * OUTPUT_PRICE, 4)
        cost_krw = round(cost_usd * KRW_RATE)

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

    return TokenUsageResponse(
        users=user_usages,
        total_input=sum(u.input_tokens for u in user_usages),
        total_output=sum(u.output_tokens for u in user_usages),
        total_tokens=sum(u.total_tokens for u in user_usages),
        total_cost_usd=round(sum(u.cost_usd for u in user_usages), 4),
        total_cost_krw=sum(u.cost_krw for u in user_usages),
        collected_at=datetime.now(timezone.utc).isoformat(),
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


class NodeInfo(BaseModel):
    node_name: str
    instance_type: str
    status: str
    cpu_capacity: str
    memory_capacity: str
    node_role: str = "user"  # "system" | "presenter" | "user"
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

        # 노드 역할 판별
        node_role = "user"
        if labels.get("role") == "presenter":
            node_role = "presenter"

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

    # User pods
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
        )

        if node_name in node_map:
            node_map[node_name].pods.append(pod_info)
        total_pods += 1

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

    from app.core.config import POD_TTL_SECONDS_MAP
    ttl = POD_TTL_SECONDS_MAP.get(user.pod_ttl, 14400)
    pod_name = k8s.create_pod(
        req.username.upper(), "daily", user.name or req.username,
        ttl_seconds=ttl, target_node=req.node_name,
    )

    # 세션 레코드
    session = db.query(TerminalSession).filter(TerminalSession.pod_name == pod_name).first()
    if session:
        session.pod_status = "creating"
        session.started_at = datetime.now(timezone.utc)
        session.terminated_at = None
    else:
        session = TerminalSession(
            user_id=user.id, username=req.username.upper(),
            pod_name=pod_name, pod_status="creating",
            session_type="daily", started_at=datetime.now(timezone.utc),
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

    # 대상 노드에 재생성
    from app.core.config import POD_TTL_SECONDS_MAP
    ttl = POD_TTL_SECONDS_MAP.get(user.pod_ttl, 14400)
    k8s.create_pod(req.username.upper(), "daily", user.name or req.username,
                   ttl_seconds=ttl, target_node=req.target_node)

    # 세션 업데이트
    session = db.query(TerminalSession).filter(TerminalSession.pod_name == pod_name).first()
    if session:
        session.pod_status = "creating"
        session.started_at = datetime.now(timezone.utc)
        session.terminated_at = None
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


@router.get("/nodegroups", response_model=NodeGroupListResponse)
async def list_nodegroups(
    _admin: dict = Depends(_require_admin),
    settings: Settings = Depends(get_settings),
):
    """EKS 노드그룹 목록 조회."""
    import boto3
    eks = boto3.client("eks", region_name="ap-northeast-2")
    cluster = "bedrock-claude-eks"

    ng_names = eks.list_nodegroups(clusterName=cluster)["nodegroups"]
    groups = []
    for ng_name in ng_names:
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
    return NodeGroupListResponse(groups=groups)


@router.post("/scale-nodegroup")
async def scale_nodegroup(
    req: ScaleNodeGroupRequest,
    _admin: dict = Depends(_require_admin),
):
    """EKS 노드그룹 스케일링."""
    import boto3
    eks = boto3.client("eks", region_name="ap-northeast-2")
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
            "maxSize": scaling["maxSize"],
            "desiredSize": req.desired_size,
        },
    )

    logger.info(f"Nodegroup {req.nodegroup_name} scaled to {req.desired_size}")
    return {
        "nodegroup": req.nodegroup_name,
        "desired_size": req.desired_size,
        "status": "scaling",
    }
