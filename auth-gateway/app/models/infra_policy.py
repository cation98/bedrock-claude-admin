"""인프라 정책 템플릿 — 노드그룹별 Pod 리소스 관리."""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, JSON
from app.core.database import Base


class InfraTemplate(Base):
    __tablename__ = "infra_templates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(String(200))
    policy = Column(JSON, nullable=False)
    created_by = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                       onupdate=lambda: datetime.now(timezone.utc))


INFRA_TEMPLATES = {
    "standard": {
        "nodegroup": "bedrock-claude-dedicated-nodes",
        "node_selector": {"role": "claude-dedicated"},
        "max_pods_per_node": 1,
        "cpu_request": "1700m",
        "cpu_limit": "1700m",
        "memory_request": "2900Mi",
        "memory_limit": "2900Mi",
        "shared_dir_writable": False,
    },
    "premium": {
        "nodegroup": "bedrock-claude-nodes",
        "node_selector": {"role": "claude-terminal"},
        "max_pods_per_node": 1,
        "cpu_request": "1",
        "cpu_limit": "1500m",
        "memory_request": "3Gi",
        "memory_limit": "6Gi",
        "shared_dir_writable": False,
    },
    "enterprise": {
        "nodegroup": "presenter-node",
        "node_selector": {"role": "presenter"},
        "max_pods_per_node": 1,
        "cpu_request": "3",
        "cpu_limit": "3500m",
        "memory_request": "8Gi",
        "memory_limit": "12Gi",
        "shared_dir_writable": True,
    },
}

INFRA_TEMPLATE_DESCRIPTIONS = {
    "standard": "기본 (t3.medium, 노드당 1명, 1:1 격리)",
    "premium": "고사양 (m5.large, 노드당 1명, CPU 1코어+)",
    "enterprise": "최고사양 (m5.xlarge, 노드당 1명, CPU 3코어+)",
}
