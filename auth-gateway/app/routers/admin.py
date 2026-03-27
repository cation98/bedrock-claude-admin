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

        node_map[name] = NodeInfo(
            node_name=name,
            instance_type=instance_type,
            status=status,
            cpu_capacity=node.status.capacity.get("cpu", "0"),
            memory_capacity=node.status.capacity.get("memory", "0"),
            pods=[],
        )

    # Pods
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
