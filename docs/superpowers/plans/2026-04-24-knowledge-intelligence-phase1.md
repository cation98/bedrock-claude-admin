# 조직 암묵지 인텔리전스 플랫폼 — Phase 1 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 `prompt_audit_conversations` 데이터에서 LLM이 지식 개념을 자동 추출하고, 어드민 대시보드에서 인터랙티브 노드 그래프와 추이 차트로 시각화한다.

**Architecture:** Claude Haiku 4.5가 매일 02:00 미처리 대화를 배치 처리해 `knowledge_nodes` / `knowledge_edges` / `knowledge_mentions` 테이블에 저장한다. `GET /api/v1/knowledge/graph` 와 `/trends` 엔드포인트가 집계 데이터를 반환하고, Next.js 프론트엔드가 React Flow로 그래프를 렌더링한다.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, boto3 (Bedrock), pytest, Next.js 15, @xyflow/react, Recharts

**Spec:** `docs/superpowers/specs/2026-04-24-knowledge-intelligence-platform-design.md`

**Phase 2 plan:** 분석 엔진 + 갭 분석 + 워크플로우 빌더 (별도 계획)

---

## 파일 구조

### 신규 생성
| 파일 | 역할 |
|------|------|
| `auth-gateway/app/models/knowledge.py` | 7개 SQLAlchemy 모델 |
| `auth-gateway/app/schemas/knowledge.py` | Pydantic 응답 스키마 |
| `auth-gateway/app/services/knowledge_extractor.py` | Bedrock Haiku 배치 추출 + DB upsert |
| `auth-gateway/app/routers/knowledge.py` | `/api/v1/knowledge/*` 엔드포인트 |
| `auth-gateway/alembic/versions/a1b2c3d4e5f6_add_knowledge_graph_tables.py` | 마이그레이션 |
| `auth-gateway/tests/test_knowledge_extractor.py` | 추출 서비스 단위 테스트 |
| `auth-gateway/tests/test_knowledge_router.py` | API 엔드포인트 통합 테스트 |
| `admin-dashboard/app/analytics/knowledge-graph/page.tsx` | 그래프 페이지 |
| `admin-dashboard/app/analytics/knowledge-trends/page.tsx` | 추이 페이지 |
| `admin-dashboard/components/KnowledgeGraph.tsx` | React Flow 래퍼 컴포넌트 |

### 수정
| 파일 | 변경 내용 |
|------|----------|
| `auth-gateway/app/main.py` | 모델 import + 라우터 등록 + 스케줄러 태스크 추가 |
| `auth-gateway/app/core/scheduler.py` | `knowledge_extraction_loop()` 추가 |
| `auth-gateway/app/models/prompt_audit.py` | `knowledge_extracted_at` 컬럼 추가 |
| `auth-gateway/tests/conftest.py` | 신규 모델 import 추가 |
| `admin-dashboard/lib/api.ts` | `fetchKnowledgeGraph`, `fetchKnowledgeTrends` 추가 |
| `admin-dashboard/components/sidebar.tsx` | NAV_ITEMS에 2개 항목 추가 |
| `admin-dashboard/package.json` | `@xyflow/react` 의존성 추가 |

---

## Task 1: SQLAlchemy 모델 정의

**Files:**
- Create: `auth-gateway/app/models/knowledge.py`

- [ ] **Step 1: 모델 파일 생성**

```python
# auth-gateway/app/models/knowledge.py
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


def _now():
    return datetime.now(timezone.utc)


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    concept_name = Column(String(200), nullable=False)
    concept_type = Column(String(50), nullable=False)  # skill|tool|domain|method|problem|topic
    normalized_name = Column(String(200), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    first_seen_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_node_id = Column(Integer, nullable=False)
    target_node_id = Column(Integer, nullable=False)
    edge_type = Column(String(50), nullable=False)  # co_occurs|precedes|enables|relates_to
    weight = Column(Float, default=1.0, nullable=False)
    co_occurrence_count = Column(Integer, default=0, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("source_node_id", "target_node_id", "edge_type"),
    )


class KnowledgeMention(Base):
    __tablename__ = "knowledge_mentions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False)
    node_id = Column(Integer, nullable=False)
    username = Column(String(100), nullable=False)
    session_id = Column(String(200), nullable=True)
    context_snippet = Column(String(200), nullable=True)
    confidence_score = Column(Float, nullable=True)
    mentioned_at = Column(DateTime(timezone=True), nullable=True)
    extracted_at = Column(DateTime(timezone=True), default=_now)


class KnowledgeSnapshot(Base):
    __tablename__ = "knowledge_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    granularity = Column(String(10), nullable=False)    # daily|weekly|monthly
    node_id = Column(Integer, nullable=False)
    mention_count = Column(Integer, default=0, nullable=False)
    unique_users = Column(Integer, default=0, nullable=False)
    unique_sessions = Column(Integer, default=0, nullable=False)
    department_breakdown = Column(JSONB, nullable=True)
    prev_mention_count = Column(Integer, nullable=True)
    growth_rate = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("snapshot_date", "granularity", "node_id"),
    )


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    created_by = Column(String(100), nullable=True)
    is_public = Column(Boolean, default=True, nullable=False)
    target_department = Column(String(100), nullable=True)
    steps = Column(JSONB, nullable=True)        # [{"id": "s1", "name": "요구수집"}]
    connections = Column(JSONB, nullable=True)  # [{"from": "s1", "to": "s2"}]
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class KnowledgeTaxonomy(Base):
    __tablename__ = "knowledge_taxonomy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    knowledge_node_id = Column(Integer, nullable=False)
    workflow_template_id = Column(Integer, nullable=False)
    workflow_step_id = Column(String(100), nullable=False)
    mapped_by = Column(String(100), nullable=True)  # 'auto' | username
    confidence_score = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("knowledge_node_id", "workflow_template_id", "workflow_step_id"),
    )


class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, nullable=True)
    username = Column(String(100), nullable=False)
    name = Column(String(200), nullable=False)
    canvas_data = Column(JSONB, nullable=True)
    is_personal = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
```

- [ ] **Step 2: PromptAuditConversation 모델에 컬럼 추가**

`auth-gateway/app/models/prompt_audit.py`에서 `PromptAuditConversation` 클래스를 찾아 끝에 추가:

```python
# PromptAuditConversation 클래스 내부 마지막 컬럼 다음에 추가
knowledge_extracted_at = Column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 3: 커밋**

```bash
git add auth-gateway/app/models/knowledge.py auth-gateway/app/models/prompt_audit.py
git commit -m "feat(knowledge): add SQLAlchemy models for knowledge graph"
```

---

## Task 2: Alembic 마이그레이션

**Files:**
- Create: `auth-gateway/alembic/versions/a1b2c3d4e5f6_add_knowledge_graph_tables.py`

- [ ] **Step 1: 마이그레이션 파일 생성**

```python
# auth-gateway/alembic/versions/a1b2c3d4e5f6_add_knowledge_graph_tables.py
"""add knowledge graph tables

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-04-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_nodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("concept_name", sa.String(200), nullable=False),
        sa.Column("concept_type", sa.String(50), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name"),
    )

    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_node_id", sa.Integer(), nullable=False),
        sa.Column("target_node_id", sa.Integer(), nullable=False),
        sa.Column("edge_type", sa.String(50), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("co_occurrence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_node_id", "target_node_id", "edge_type"),
    )

    op.create_table(
        "knowledge_mentions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("session_id", sa.String(200), nullable=True),
        sa.Column("context_snippet", sa.String(200), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("mentioned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_mentions_node_id", "knowledge_mentions", ["node_id"])
    op.create_index("ix_knowledge_mentions_username", "knowledge_mentions", ["username"])

    op.create_table(
        "knowledge_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_date", sa.String(10), nullable=False),
        sa.Column("granularity", sa.String(10), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("mention_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("department_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prev_mention_count", sa.Integer(), nullable=True),
        sa.Column("growth_rate", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_date", "granularity", "node_id"),
    )

    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("target_department", sa.String(100), nullable=True),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("connections", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "knowledge_taxonomy",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("knowledge_node_id", sa.Integer(), nullable=False),
        sa.Column("workflow_template_id", sa.Integer(), nullable=False),
        sa.Column("workflow_step_id", sa.String(100), nullable=False),
        sa.Column("mapped_by", sa.String(100), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_node_id", "workflow_template_id", "workflow_step_id"),
    )

    op.create_table(
        "workflow_instances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("canvas_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_personal", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # prompt_audit_conversations에 knowledge_extracted_at 컬럼 추가
    op.add_column(
        "prompt_audit_conversations",
        sa.Column("knowledge_extracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_pac_knowledge_extracted_at",
        "prompt_audit_conversations",
        ["knowledge_extracted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pac_knowledge_extracted_at", "prompt_audit_conversations")
    op.drop_column("prompt_audit_conversations", "knowledge_extracted_at")
    op.drop_table("workflow_instances")
    op.drop_table("knowledge_taxonomy")
    op.drop_table("workflow_templates")
    op.drop_table("knowledge_snapshots")
    op.drop_index("ix_knowledge_mentions_username", "knowledge_mentions")
    op.drop_index("ix_knowledge_mentions_node_id", "knowledge_mentions")
    op.drop_table("knowledge_mentions")
    op.drop_table("knowledge_edges")
    op.drop_table("knowledge_nodes")
```

- [ ] **Step 2: 마이그레이션 실행**

```bash
cd auth-gateway
alembic upgrade head
```

Expected: `Running upgrade f6a7b8c9d0e1 -> a1b2c3d4e5f6, add knowledge graph tables`

- [ ] **Step 3: 테이블 생성 확인**

```bash
python -c "
from app.core.database import engine
from sqlalchemy import inspect
insp = inspect(engine)
print(insp.get_table_names())
"
```

Expected: `knowledge_nodes`, `knowledge_edges`, `knowledge_mentions`, `knowledge_snapshots`, `workflow_templates`, `knowledge_taxonomy`, `workflow_instances` 포함 확인

- [ ] **Step 4: 커밋**

```bash
git add auth-gateway/alembic/versions/a1b2c3d4e5f6_add_knowledge_graph_tables.py
git commit -m "feat(knowledge): alembic migration — add 7 knowledge graph tables"
```

---

## Task 3: Pydantic 스키마

**Files:**
- Create: `auth-gateway/app/schemas/knowledge.py`

- [ ] **Step 1: 스키마 파일 생성**

```python
# auth-gateway/app/schemas/knowledge.py
from typing import Any
from pydantic import BaseModel


class KnowledgeNodeOut(BaseModel):
    id: int
    concept_name: str
    concept_type: str
    normalized_name: str
    mention_count: int = 0

    model_config = {"from_attributes": True}


class KnowledgeEdgeOut(BaseModel):
    id: int
    source_node_id: int
    target_node_id: int
    edge_type: str
    weight: float
    co_occurrence_count: int

    model_config = {"from_attributes": True}


class KnowledgeGraphResponse(BaseModel):
    nodes: list[KnowledgeNodeOut]
    edges: list[KnowledgeEdgeOut]
    total_nodes: int
    total_edges: int


class KnowledgeTrendNode(BaseModel):
    id: int
    concept_name: str
    concept_type: str
    trend: str           # emerging | rising | stable | declining
    growth_rate: float | None
    weekly_counts: list[int]  # 최근 12주


class KnowledgeTrendsResponse(BaseModel):
    nodes: list[KnowledgeTrendNode]
    period_weeks: int
```

- [ ] **Step 2: 커밋**

```bash
git add auth-gateway/app/schemas/knowledge.py
git commit -m "feat(knowledge): add Pydantic response schemas"
```

---

## Task 4: 지식 추출 서비스

**Files:**
- Create: `auth-gateway/app/services/knowledge_extractor.py`
- Create: `auth-gateway/tests/test_knowledge_extractor.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# auth-gateway/tests/test_knowledge_extractor.py
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.knowledge_extractor import (
    normalize_name,
    parse_extraction_response,
    group_conversations_by_session,
)


def test_normalize_name_lowercases_and_strips():
    assert normalize_name("  Python Pandas  ") == "python pandas"


def test_normalize_name_removes_special_chars():
    assert normalize_name("Docker-컨테이너(최적화)") == "docker 컨테이너 최적화"


def test_parse_extraction_response_valid():
    raw = json.dumps({
        "concepts": [
            {"name": "Python pandas", "type": "tool", "confidence": 0.9},
            {"name": "데이터 시각화", "type": "skill", "confidence": 0.8},
        ],
        "relationships": [
            {"source": "Python pandas", "target": "데이터 시각화", "type": "co_occurs"}
        ],
    })
    result = parse_extraction_response(raw)
    assert len(result["concepts"]) == 2
    assert result["concepts"][0]["name"] == "Python pandas"
    assert len(result["relationships"]) == 1


def test_parse_extraction_response_invalid_json_returns_empty():
    result = parse_extraction_response("not json")
    assert result == {"concepts": [], "relationships": []}


def test_parse_extraction_response_missing_keys_returns_empty():
    result = parse_extraction_response(json.dumps({"other": "data"}))
    assert result["concepts"] == []
    assert result["relationships"] == []


def test_group_conversations_by_session():
    convs = [
        MagicMock(session_id="s1", content="hello", username="u1"),
        MagicMock(session_id="s1", content="world", username="u1"),
        MagicMock(session_id="s2", content="foo", username="u2"),
    ]
    groups = group_conversations_by_session(convs)
    assert len(groups) == 2
    assert len(groups["s1"]) == 2
    assert len(groups["s2"]) == 1
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
cd auth-gateway
python -m pytest tests/test_knowledge_extractor.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` (파일 없으므로)

- [ ] **Step 3: 서비스 구현**

```python
# auth-gateway/app/services/knowledge_extractor.py
"""암묵지 추출 서비스 — Claude Haiku 4.5 via AWS Bedrock."""
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

import boto3
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeEdge, KnowledgeMention, KnowledgeNode
from app.models.prompt_audit import PromptAuditConversation

logger = logging.getLogger(__name__)

HAIKU_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
BATCH_SIZE = 8      # 한 번에 처리할 세션 수
MAX_CONVERSATIONS = 500  # 1회 실행 상한선

_SYSTEM_PROMPT = (
    "다음 AI 대화 내용에서 사용자가 다루는 지식 개념, 기술, 도구, 방법론을 추출하라. "
    "반드시 아래 JSON 형식으로만 응답하고 다른 텍스트는 포함하지 않는다.\n"
    '{"concepts": [{"name": "개념명", "type": "skill|tool|domain|method|problem|topic", "confidence": 0.0~1.0}], '
    '"relationships": [{"source": "개념A", "target": "개념B", "type": "co_occurs|precedes|enables|relates_to"}]}'
)


def normalize_name(name: str) -> str:
    """개념명을 소문자+공백 정규화 (dedup 키로 사용)."""
    name = name.lower().strip()
    name = re.sub(r"[^\w\s가-힣]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_extraction_response(raw: str) -> dict:
    """Bedrock 응답 JSON 파싱. 실패 시 빈 dict 반환."""
    try:
        data = json.loads(raw)
        if "concepts" not in data:
            return {"concepts": [], "relationships": []}
        if "relationships" not in data:
            data["relationships"] = []
        return data
    except (json.JSONDecodeError, TypeError):
        return {"concepts": [], "relationships": []}


def group_conversations_by_session(conversations: list) -> dict:
    """conversation 목록을 session_id별로 그룹핑."""
    groups: dict[str, list] = defaultdict(list)
    for conv in conversations:
        key = conv.session_id or f"no-session-{conv.id}"
        groups[key].append(conv)
    return dict(groups)


def _build_prompt(sessions: dict[str, list]) -> str:
    """세션 그룹을 하나의 프롬프트 텍스트로 합산."""
    lines = []
    for session_id, convs in sessions.items():
        lines.append(f"[세션 {session_id}]")
        for c in convs:
            role = "사용자" if c.message_type == "user" else "AI"
            snippet = (c.content or "")[:300]
            lines.append(f"{role}: {snippet}")
    return "\n".join(lines)


def _call_haiku(prompt_text: str, region: str = "us-east-1") -> str:
    """AWS Bedrock converse API로 Claude Haiku 호출."""
    client = boto3.client("bedrock-runtime", region_name=region)
    response = client.converse(
        modelId=HAIKU_MODEL_ID,
        system=[{"text": _SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": prompt_text}]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0.0},
    )
    return response["output"]["message"]["content"][0]["text"]


def _upsert_node(db: Session, concept: dict, now: datetime) -> KnowledgeNode | None:
    """개념을 knowledge_nodes에 upsert. normalized_name 기준 중복 병합."""
    name = (concept.get("name") or "").strip()
    ctype = concept.get("type", "topic")
    if not name:
        return None
    normalized = normalize_name(name)
    node = db.query(KnowledgeNode).filter_by(normalized_name=normalized).first()
    if node is None:
        node = KnowledgeNode(
            concept_name=name,
            concept_type=ctype,
            normalized_name=normalized,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(node)
        db.flush()
    else:
        node.last_seen_at = now
    return node


def _upsert_edge(
    db: Session, source_id: int, target_id: int, edge_type: str, now: datetime
) -> None:
    """엣지 upsert — 이미 있으면 co_occurrence_count 증가."""
    edge = (
        db.query(KnowledgeEdge)
        .filter_by(source_node_id=source_id, target_node_id=target_id, edge_type=edge_type)
        .first()
    )
    if edge is None:
        edge = KnowledgeEdge(
            source_node_id=source_id,
            target_node_id=target_id,
            edge_type=edge_type,
            co_occurrence_count=1,
            last_seen_at=now,
        )
        db.add(edge)
    else:
        edge.co_occurrence_count = (edge.co_occurrence_count or 0) + 1
        edge.weight = min(float(edge.co_occurrence_count) / 10.0, 5.0)
        edge.last_seen_at = now


def run_extraction(db: Session, region: str = "us-east-1") -> int:
    """미처리 대화를 배치 처리하여 knowledge 테이블에 저장. 처리한 대화 수 반환."""
    conversations = (
        db.query(PromptAuditConversation)
        .filter(PromptAuditConversation.knowledge_extracted_at.is_(None))
        .order_by(PromptAuditConversation.timestamp.asc())
        .limit(MAX_CONVERSATIONS)
        .all()
    )
    if not conversations:
        logger.info("knowledge extraction: no unprocessed conversations")
        return 0

    session_groups = group_conversations_by_session(conversations)
    session_keys = list(session_groups.keys())
    now = datetime.now(timezone.utc)
    processed_conv_ids: list[int] = []

    for batch_start in range(0, len(session_keys), BATCH_SIZE):
        batch_keys = session_keys[batch_start: batch_start + BATCH_SIZE]
        batch = {k: session_groups[k] for k in batch_keys}
        prompt_text = _build_prompt(batch)

        for attempt in range(3):
            try:
                raw = _call_haiku(prompt_text, region=region)
                break
            except Exception as exc:
                if attempt == 2:
                    logger.error(f"knowledge extraction batch failed after 3 tries: {exc}")
                    raw = "{}"
                    break
                logger.warning(f"knowledge extraction attempt {attempt+1} failed: {exc}")

        extracted = parse_extraction_response(raw)

        # 노드 upsert
        node_map: dict[str, KnowledgeNode] = {}
        for concept in extracted.get("concepts", []):
            node = _upsert_node(db, concept, now)
            if node:
                node_map[normalize_name(concept["name"])] = node

        # 엣지 upsert
        for rel in extracted.get("relationships", []):
            src_key = normalize_name(rel.get("source", ""))
            tgt_key = normalize_name(rel.get("target", ""))
            edge_type = rel.get("type", "co_occurs")
            if src_key in node_map and tgt_key in node_map:
                _upsert_edge(db, node_map[src_key].id, node_map[tgt_key].id, edge_type, now)

        # mention 연결
        for session_key, convs in batch.items():
            for conv in convs:
                for concept in extracted.get("concepts", []):
                    normalized = normalize_name(concept.get("name", ""))
                    node = node_map.get(normalized)
                    if node:
                        snippet = (conv.content or "")[:30]
                        mention = KnowledgeMention(
                            conversation_id=conv.id,
                            node_id=node.id,
                            username=conv.username,
                            session_id=conv.session_id,
                            context_snippet=snippet,
                            confidence_score=concept.get("confidence"),
                            mentioned_at=conv.timestamp,
                        )
                        db.add(mention)
                processed_conv_ids.append(conv.id)

        db.flush()

    # 처리 완료 마킹
    if processed_conv_ids:
        db.query(PromptAuditConversation).filter(
            PromptAuditConversation.id.in_(processed_conv_ids)
        ).update({"knowledge_extracted_at": now}, synchronize_session=False)

    db.commit()
    logger.info(f"knowledge extraction complete: {len(processed_conv_ids)} conversations")
    return len(processed_conv_ids)
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
cd auth-gateway
python -m pytest tests/test_knowledge_extractor.py -v
```

Expected:
```
PASSED test_normalize_name_lowercases_and_strips
PASSED test_normalize_name_removes_special_chars
PASSED test_parse_extraction_response_valid
PASSED test_parse_extraction_response_invalid_json_returns_empty
PASSED test_parse_extraction_response_missing_keys_returns_empty
PASSED test_group_conversations_by_session
```

- [ ] **Step 5: 커밋**

```bash
git add auth-gateway/app/services/knowledge_extractor.py auth-gateway/tests/test_knowledge_extractor.py
git commit -m "feat(knowledge): add knowledge extractor service with unit tests"
```

---

## Task 5: Knowledge API 라우터

**Files:**
- Create: `auth-gateway/app/routers/knowledge.py`
- Create: `auth-gateway/tests/test_knowledge_router.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# auth-gateway/tests/test_knowledge_router.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.knowledge import KnowledgeNode, KnowledgeEdge, KnowledgeMention  # noqa: F401
from app.models.prompt_audit import PromptAuditConversation, PromptAuditSummary, PromptAuditFlag  # noqa: F401
from app.routers.knowledge import router


# SQLite in-memory test DB
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

# JSONB → TEXT compatibility (same as conftest.py)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator, Text
import json

class _JSONBtoText(TypeDecorator):
    impl = Text
    cache_ok = True
    def process_bind_param(self, value, dialect):
        return json.dumps(value) if value is not None and not isinstance(value, str) else value
    def process_result_value(self, value, dialect):
        if value is not None and isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

for _table in Base.metadata.tables.values():
    for _col in _table.columns:
        if isinstance(_col.type, JSONB):
            _col.type = _JSONBtoText()

TestSession = sessionmaker(bind=engine)
app = FastAPI()
app.include_router(router)


@pytest.fixture(autouse=True)
def _tables():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: {"sub": "ADMIN01", "role": "admin"}
    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc
    app.dependency_overrides.clear()


def test_get_graph_empty(client):
    res = client.get("/api/v1/knowledge/graph")
    assert res.status_code == 200
    data = res.json()
    assert data["nodes"] == []
    assert data["edges"] == []
    assert data["total_nodes"] == 0


def test_get_graph_returns_nodes_and_edges(client, db):
    n1 = KnowledgeNode(concept_name="Python", concept_type="tool", normalized_name="python")
    n2 = KnowledgeNode(concept_name="pandas", concept_type="tool", normalized_name="pandas")
    db.add_all([n1, n2])
    db.flush()
    edge = KnowledgeEdge(source_node_id=n1.id, target_node_id=n2.id, edge_type="co_occurs", weight=2.0, co_occurrence_count=2)
    db.add(edge)
    db.commit()

    res = client.get("/api/v1/knowledge/graph")
    assert res.status_code == 200
    data = res.json()
    assert data["total_nodes"] == 2
    assert data["total_edges"] == 1


def test_get_graph_requires_admin(client):
    from app.core.security import get_current_user as gcu
    app.dependency_overrides[gcu] = lambda: {"sub": "USER01", "role": "user"}
    res = client.get("/api/v1/knowledge/graph")
    assert res.status_code == 403
    app.dependency_overrides[gcu] = lambda: {"sub": "ADMIN01", "role": "admin"}


def test_get_trends_empty(client):
    res = client.get("/api/v1/knowledge/trends")
    assert res.status_code == 200
    data = res.json()
    assert data["nodes"] == []
    assert data["period_weeks"] == 12
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
cd auth-gateway
python -m pytest tests/test_knowledge_router.py -v
```

Expected: `ImportError` (라우터 없으므로)

- [ ] **Step 3: 라우터 구현**

```python
# auth-gateway/app/routers/knowledge.py
"""지식 그래프 API — /api/v1/knowledge/*"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.knowledge import KnowledgeEdge, KnowledgeMention, KnowledgeNode, KnowledgeSnapshot
from app.schemas.knowledge import (
    KnowledgeEdgeOut,
    KnowledgeGraphResponse,
    KnowledgeNodeOut,
    KnowledgeTrendNode,
    KnowledgeTrendsResponse,
)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/graph", response_model=KnowledgeGraphResponse)
def get_knowledge_graph(
    concept_type: str | None = None,
    min_mentions: int = 1,
    db: Session = Depends(get_db),
    _admin: dict = Depends(_require_admin),
) -> KnowledgeGraphResponse:
    """지식 그래프 — 노드와 엣지를 반환."""
    # 노드별 언급 수 집계
    mention_counts = (
        db.query(KnowledgeMention.node_id, func.count().label("cnt"))
        .group_by(KnowledgeMention.node_id)
        .subquery()
    )

    node_query = db.query(KnowledgeNode, mention_counts.c.cnt).outerjoin(
        mention_counts, KnowledgeNode.id == mention_counts.c.node_id
    ).filter(KnowledgeNode.is_active.is_(True))

    if concept_type:
        node_query = node_query.filter(KnowledgeNode.concept_type == concept_type)

    node_rows = node_query.all()
    active_node_ids = set()
    nodes_out: list[KnowledgeNodeOut] = []

    for node, cnt in node_rows:
        count = cnt or 0
        if count >= min_mentions:
            active_node_ids.add(node.id)
            nodes_out.append(
                KnowledgeNodeOut(
                    id=node.id,
                    concept_name=node.concept_name,
                    concept_type=node.concept_type,
                    normalized_name=node.normalized_name,
                    mention_count=count,
                )
            )

    edges_out: list[KnowledgeEdgeOut] = []
    if active_node_ids:
        edges = (
            db.query(KnowledgeEdge)
            .filter(
                KnowledgeEdge.source_node_id.in_(active_node_ids),
                KnowledgeEdge.target_node_id.in_(active_node_ids),
            )
            .all()
        )
        edges_out = [
            KnowledgeEdgeOut(
                id=e.id,
                source_node_id=e.source_node_id,
                target_node_id=e.target_node_id,
                edge_type=e.edge_type,
                weight=e.weight or 1.0,
                co_occurrence_count=e.co_occurrence_count or 0,
            )
            for e in edges
        ]

    return KnowledgeGraphResponse(
        nodes=nodes_out,
        edges=edges_out,
        total_nodes=len(nodes_out),
        total_edges=len(edges_out),
    )


@router.get("/trends", response_model=KnowledgeTrendsResponse)
def get_knowledge_trends(
    weeks: int = 12,
    db: Session = Depends(get_db),
    _admin: dict = Depends(_require_admin),
) -> KnowledgeTrendsResponse:
    """최근 N주간 지식 추이 — 노드별 growth_rate와 주간 언급 수 반환."""
    snapshots = (
        db.query(KnowledgeSnapshot)
        .filter(KnowledgeSnapshot.granularity == "weekly")
        .order_by(KnowledgeSnapshot.snapshot_date.desc())
        .limit(1000)
        .all()
    )

    # node_id별 주간 데이터 집계
    node_snapshots: dict[int, list[KnowledgeSnapshot]] = {}
    for s in snapshots:
        node_snapshots.setdefault(s.node_id, []).append(s)

    trend_nodes: list[KnowledgeTrendNode] = []
    for node_id, snaps in node_snapshots.items():
        snaps_sorted = sorted(snaps, key=lambda x: x.snapshot_date)[-weeks:]
        weekly_counts = [s.mention_count for s in snaps_sorted]

        latest = snaps_sorted[-1] if snaps_sorted else None
        growth_rate = latest.growth_rate if latest else None

        if growth_rate is None:
            trend = "stable"
        elif growth_rate > 0.30:
            trend = "emerging"
        elif growth_rate > 0.15:
            trend = "rising"
        elif growth_rate < -0.20:
            trend = "declining"
        else:
            trend = "stable"

        node = db.query(KnowledgeNode).filter_by(id=node_id).first()
        if not node:
            continue

        trend_nodes.append(
            KnowledgeTrendNode(
                id=node.id,
                concept_name=node.concept_name,
                concept_type=node.concept_type,
                trend=trend,
                growth_rate=growth_rate,
                weekly_counts=weekly_counts,
            )
        )

    # growth_rate 내림차순 정렬
    trend_nodes.sort(key=lambda n: (n.growth_rate or 0), reverse=True)

    return KnowledgeTrendsResponse(nodes=trend_nodes, period_weeks=weeks)
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
cd auth-gateway
python -m pytest tests/test_knowledge_router.py -v
```

Expected:
```
PASSED test_get_graph_empty
PASSED test_get_graph_returns_nodes_and_edges
PASSED test_get_graph_requires_admin
PASSED test_get_trends_empty
```

- [ ] **Step 5: 커밋**

```bash
git add auth-gateway/app/routers/knowledge.py auth-gateway/tests/test_knowledge_router.py
git commit -m "feat(knowledge): add /graph and /trends API endpoints with tests"
```

---

## Task 6: main.py 등록

**Files:**
- Modify: `auth-gateway/app/main.py`

- [ ] **Step 1: 모델 import 추가**

`auth-gateway/app/main.py`에서 기존 모델 import 블록 바로 아래에 추가:

```python
from app.models.knowledge import (  # noqa: F401 — create_all이 knowledge 테이블 생성하도록 import
    KnowledgeNode, KnowledgeEdge, KnowledgeMention,
    KnowledgeSnapshot, WorkflowTemplate, KnowledgeTaxonomy, WorkflowInstance,
)
```

- [ ] **Step 2: 라우터 등록**

기존 `app.include_router(guides_router)` 바로 아래에 추가:

```python
from app.routers.knowledge import router as knowledge_router
app.include_router(knowledge_router)
```

- [ ] **Step 3: 전체 테스트 통과 확인**

```bash
cd auth-gateway
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: 기존 테스트 + 신규 테스트 모두 통과

- [ ] **Step 4: 커밋**

```bash
git add auth-gateway/app/main.py
git commit -m "feat(knowledge): register models and router in main.py"
```

---

## Task 7: 스케줄러 루프

**Files:**
- Modify: `auth-gateway/app/core/scheduler.py`
- Modify: `auth-gateway/app/main.py` (태스크 생성)

- [ ] **Step 1: 스케줄러 루프 추가**

`auth-gateway/app/core/scheduler.py` 파일 끝에 추가:

```python
async def knowledge_extraction_loop(settings: Settings) -> None:
    """백그라운드 루프: 6시간마다 미처리 대화를 지식 그래프로 추출한다."""
    from app.core.database import SessionLocal
    from app.services.knowledge_extractor import run_extraction

    logger.info("knowledge extraction scheduler started — interval=6h")
    await asyncio.sleep(30)  # 앱 기동 안정화 대기

    while True:
        if acquire_scheduler_lock("knowledge_extraction", ttl_seconds=3600 * 6):
            try:
                async with SessionLocal() as db:
                    count = await asyncio.get_event_loop().run_in_executor(
                        None, run_extraction, db, "us-east-1"
                    )
                    logger.info(f"knowledge extraction done: {count} conversations processed")
            except Exception as exc:
                logger.error(f"knowledge extraction loop error: {exc}")
            finally:
                release_scheduler_lock("knowledge_extraction")
        await asyncio.sleep(6 * 3600)
```

- [ ] **Step 2: main.py lifespan에 태스크 추가**

`auth-gateway/app/main.py`에서 `storage_task = asyncio.create_task(...)` 바로 아래에 추가:

```python
knowledge_task = asyncio.create_task(knowledge_extraction_loop(settings))
```

그리고 `yield` 이후 `storage_task.cancel()` 바로 아래에 추가:

```python
knowledge_task.cancel()
```

`try: await storage_task` 블록 패턴을 따라 아래에도 추가:

```python
try:
    await knowledge_task
except asyncio.CancelledError:
    pass
```

- [ ] **Step 3: scheduler import 추가**

`auth-gateway/app/main.py`에서 scheduler import 라인에 추가:

```python
from app.core.scheduler import (
    idle_checker_loop,
    token_snapshot_loop,
    prompt_audit_loop,
    storage_cleanup_loop,
    knowledge_extraction_loop,  # 추가
)
```

- [ ] **Step 4: 커밋**

```bash
git add auth-gateway/app/core/scheduler.py auth-gateway/app/main.py
git commit -m "feat(knowledge): add knowledge_extraction_loop to scheduler"
```

---

## Task 8: conftest.py 업데이트

**Files:**
- Modify: `auth-gateway/tests/conftest.py`

- [ ] **Step 1: 신규 모델 import 추가**

`auth-gateway/tests/conftest.py`에서 기존 모델 import 블록에 추가:

```python
from app.models.knowledge import (  # noqa: F401
    KnowledgeNode, KnowledgeEdge, KnowledgeMention,
    KnowledgeSnapshot, WorkflowTemplate, KnowledgeTaxonomy, WorkflowInstance,
)
from app.models.prompt_audit import PromptAuditConversation  # noqa: F401 (이미 있으면 skip)
```

- [ ] **Step 2: 전체 테스트 통과 확인**

```bash
cd auth-gateway
python -m pytest tests/ -v --tb=short 2>&1 | tail -10
```

Expected: 모두 PASSED

- [ ] **Step 3: 커밋**

```bash
git add auth-gateway/tests/conftest.py
git commit -m "test(knowledge): register knowledge models in conftest for create_all"
```

---

## Task 9: Frontend — npm install + API 함수

**Files:**
- Modify: `admin-dashboard/package.json` (npm install로 자동 수정)
- Modify: `admin-dashboard/lib/api.ts`

- [ ] **Step 1: @xyflow/react 설치**

```bash
cd admin-dashboard
npm install @xyflow/react
```

Expected: `package.json`에 `"@xyflow/react": "^12.x.x"` 추가됨

- [ ] **Step 2: API 함수 추가**

`admin-dashboard/lib/api.ts` 끝에 추가:

```typescript
// ==================== Knowledge Graph API ====================

export interface KnowledgeNodeData {
  id: number;
  concept_name: string;
  concept_type: string;
  normalized_name: string;
  mention_count: number;
}

export interface KnowledgeEdgeData {
  id: number;
  source_node_id: number;
  target_node_id: number;
  edge_type: string;
  weight: number;
  co_occurrence_count: number;
}

export interface KnowledgeGraphData {
  nodes: KnowledgeNodeData[];
  edges: KnowledgeEdgeData[];
  total_nodes: number;
  total_edges: number;
}

export interface KnowledgeTrendNodeData {
  id: number;
  concept_name: string;
  concept_type: string;
  trend: "emerging" | "rising" | "stable" | "declining";
  growth_rate: number | null;
  weekly_counts: number[];
}

export interface KnowledgeTrendsData {
  nodes: KnowledgeTrendNodeData[];
  period_weeks: number;
}

export function fetchKnowledgeGraph(
  conceptType?: string,
  minMentions: number = 1
): Promise<KnowledgeGraphData> {
  const params = new URLSearchParams();
  if (conceptType) params.set("concept_type", conceptType);
  params.set("min_mentions", String(minMentions));
  return request<KnowledgeGraphData>(`/api/v1/knowledge/graph?${params}`);
}

export function fetchKnowledgeTrends(weeks: number = 12): Promise<KnowledgeTrendsData> {
  return request<KnowledgeTrendsData>(`/api/v1/knowledge/trends?weeks=${weeks}`);
}
```

- [ ] **Step 3: 커밋**

```bash
cd admin-dashboard
git add package.json package-lock.json lib/api.ts
git commit -m "feat(knowledge): install @xyflow/react and add knowledge API functions"
```

---

## Task 10: KnowledgeGraph 컴포넌트

**Files:**
- Create: `admin-dashboard/components/KnowledgeGraph.tsx`

- [ ] **Step 1: 컴포넌트 생성**

```tsx
// admin-dashboard/components/KnowledgeGraph.tsx
"use client";

import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { KnowledgeGraphData, KnowledgeNodeData } from "@/lib/api";

const TYPE_COLORS: Record<string, string> = {
  skill: "#6366f1",
  tool: "#10b981",
  domain: "#f59e0b",
  method: "#ec4899",
  problem: "#ef4444",
  topic: "#8b5cf6",
};

function toFlowNodes(nodes: KnowledgeGraphData["nodes"]): Node[] {
  const cols = Math.ceil(Math.sqrt(nodes.length));
  return nodes.map((n, i) => ({
    id: String(n.id),
    position: { x: (i % cols) * 180, y: Math.floor(i / cols) * 120 },
    data: {
      label: n.concept_name,
      type: n.concept_type,
      mentions: n.mention_count,
    },
    style: {
      background: TYPE_COLORS[n.concept_type] ?? "#64748b",
      color: "#fff",
      border: "none",
      borderRadius: "8px",
      fontSize: "11px",
      padding: "6px 10px",
      width: Math.max(80, Math.min(160, n.mention_count * 8 + 60)),
    },
  }));
}

function toFlowEdges(edges: KnowledgeGraphData["edges"]): Edge[] {
  return edges.map((e) => ({
    id: `e${e.id}`,
    source: String(e.source_node_id),
    target: String(e.target_node_id),
    label: e.edge_type,
    style: { strokeWidth: Math.min(e.weight, 4), stroke: "#94a3b8" },
    labelStyle: { fontSize: "9px", fill: "#94a3b8" },
  }));
}

interface Props {
  data: KnowledgeGraphData;
  onNodeClick?: (node: KnowledgeNodeData) => void;
}

export default function KnowledgeGraph({ data, onNodeClick }: Props) {
  const initialNodes = useMemo(() => toFlowNodes(data.nodes), [data.nodes]);
  const initialEdges = useMemo(() => toFlowEdges(data.edges), [data.edges]);
  const [nodes, , onNodesChange] = useNodesState(initialNodes);
  const [edges, , onEdgesChange] = useEdgesState(initialEdges);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const original = data.nodes.find((n) => String(n.id) === node.id);
      if (original && onNodeClick) onNodeClick(original);
    },
    [data.nodes, onNodeClick]
  );

  return (
    <div style={{ width: "100%", height: "600px", background: "#0f172a", borderRadius: "8px" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        fitView
        colorMode="dark"
      >
        <Background color="#1e293b" />
        <Controls />
        <MiniMap nodeColor={(n) => TYPE_COLORS[(n.data?.type as string) ?? ""] ?? "#64748b"} />
      </ReactFlow>
    </div>
  );
}
```

- [ ] **Step 2: 커밋**

```bash
git add admin-dashboard/components/KnowledgeGraph.tsx
git commit -m "feat(knowledge): add KnowledgeGraph React Flow component"
```

---

## Task 11: /analytics/knowledge-graph 페이지

**Files:**
- Create: `admin-dashboard/app/analytics/knowledge-graph/page.tsx`

- [ ] **Step 1: 페이지 생성**

```tsx
// admin-dashboard/app/analytics/knowledge-graph/page.tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { fetchKnowledgeGraph, type KnowledgeGraphData, type KnowledgeNodeData } from "@/lib/api";

// ReactFlow는 SSR 비호환 — dynamic import로 client-only 로드
const KnowledgeGraph = dynamic(() => import("@/components/KnowledgeGraph"), { ssr: false });

const CONCEPT_TYPES = ["", "skill", "tool", "domain", "method", "problem", "topic"];

export default function KnowledgeGraphPage() {
  const [data, setData] = useState<KnowledgeGraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conceptType, setConceptType] = useState("");
  const [minMentions, setMinMentions] = useState(1);
  const [selected, setSelected] = useState<KnowledgeNodeData | null>(null);

  const loadGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchKnowledgeGraph(conceptType || undefined, minMentions);
      setData(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "알 수 없는 오류");
    } finally {
      setLoading(false);
    }
  }, [conceptType, minMentions]);

  useEffect(() => { loadGraph(); }, [loadGraph]);

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)]">조직 지식 그래프</h1>
          <p className="text-sm text-[var(--text-muted)]">
            전 직원 AI 대화에서 자동 추출된 지식 개념 네트워크
          </p>
        </div>
        <button
          onClick={loadGraph}
          disabled={loading}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {loading ? "로딩 중..." : "새로고침"}
        </button>
      </div>

      {/* 필터 */}
      <div className="mb-4 flex gap-3 items-center flex-wrap">
        <select
          value={conceptType}
          onChange={(e) => setConceptType(e.target.value)}
          className="rounded border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-sm text-[var(--text-primary)]"
        >
          {CONCEPT_TYPES.map((t) => (
            <option key={t} value={t}>{t || "전체 유형"}</option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
          최소 언급 수:
          <input
            type="number"
            min={1}
            max={100}
            value={minMentions}
            onChange={(e) => setMinMentions(Number(e.target.value))}
            className="w-16 rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-sm text-[var(--text-primary)]"
          />
        </label>
        {data && (
          <span className="text-sm text-[var(--text-muted)]">
            노드 {data.total_nodes}개 · 엣지 {data.total_edges}개
          </span>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded bg-red-900/30 p-3 text-sm text-red-300">{error}</div>
      )}

      {data && data.total_nodes === 0 && !loading && (
        <div className="rounded bg-[var(--surface)] p-8 text-center text-[var(--text-muted)]">
          아직 추출된 지식 개념이 없습니다. 스케줄러가 내일 02:00에 처음 실행됩니다.
        </div>
      )}

      <div className="flex gap-4">
        {/* 그래프 */}
        <div className="flex-1">
          {data && data.total_nodes > 0 && (
            <KnowledgeGraph data={data} onNodeClick={setSelected} />
          )}
        </div>

        {/* 선택 노드 상세 */}
        {selected && (
          <div className="w-64 rounded border border-[var(--border)] bg-[var(--surface)] p-4 text-sm">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-semibold text-[var(--text-primary)]">노드 상세</span>
              <button onClick={() => setSelected(null)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)]">✕</button>
            </div>
            <div className="space-y-1 text-[var(--text-muted)]">
              <div><span className="text-[var(--text-secondary)]">이름:</span> {selected.concept_name}</div>
              <div><span className="text-[var(--text-secondary)]">유형:</span> {selected.concept_type}</div>
              <div><span className="text-[var(--text-secondary)]">언급 수:</span> {selected.mention_count}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 커밋**

```bash
git add admin-dashboard/app/analytics/knowledge-graph/page.tsx
git commit -m "feat(knowledge): add /analytics/knowledge-graph page"
```

---

## Task 12: /analytics/knowledge-trends 페이지

**Files:**
- Create: `admin-dashboard/app/analytics/knowledge-trends/page.tsx`

- [ ] **Step 1: 페이지 생성**

```tsx
// admin-dashboard/app/analytics/knowledge-trends/page.tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchKnowledgeTrends, type KnowledgeTrendsData, type KnowledgeTrendNodeData } from "@/lib/api";

const TREND_COLORS: Record<string, string> = {
  emerging: "#10b981",
  rising: "#6366f1",
  stable: "#64748b",
  declining: "#ef4444",
};

const TREND_LABELS: Record<string, string> = {
  emerging: "🚀 Emerging",
  rising: "📈 Rising",
  stable: "➡ Stable",
  declining: "📉 Declining",
};

function Sparkline({ counts }: { counts: number[] }) {
  if (counts.length === 0) return null;
  const max = Math.max(...counts, 1);
  const w = 80;
  const h = 24;
  const pts = counts
    .map((v, i) => `${(i / (counts.length - 1)) * w},${h - (v / max) * h}`)
    .join(" ");
  return (
    <svg width={w} height={h} className="opacity-70">
      <polyline points={pts} fill="none" stroke="#6366f1" strokeWidth="1.5" />
    </svg>
  );
}

export default function KnowledgeTrendsPage() {
  const [data, setData] = useState<KnowledgeTrendsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("all");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchKnowledgeTrends(12));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "오류 발생");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = data
    ? filter === "all"
      ? data.nodes
      : data.nodes.filter((n) => n.trend === filter)
    : [];

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)]">지식 추이 분석</h1>
          <p className="text-sm text-[var(--text-muted)]">최근 12주간 개념별 언급 추이</p>
        </div>
        <button onClick={load} disabled={loading} className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500 disabled:opacity-50">
          {loading ? "로딩 중..." : "새로고침"}
        </button>
      </div>

      {/* 추이 필터 탭 */}
      <div className="mb-4 flex gap-2">
        {["all", "emerging", "rising", "stable", "declining"].map((t) => (
          <button
            key={t}
            onClick={() => setFilter(t)}
            className={`rounded px-3 py-1 text-sm ${
              filter === t
                ? "bg-indigo-600 text-white"
                : "bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-hover)]"
            }`}
          >
            {t === "all" ? "전체" : TREND_LABELS[t]}
          </button>
        ))}
      </div>

      {error && <div className="mb-4 rounded bg-red-900/30 p-3 text-sm text-red-300">{error}</div>}

      {filtered.length === 0 && !loading && (
        <div className="rounded bg-[var(--surface)] p-8 text-center text-[var(--text-muted)]">
          {data ? "해당 추이 데이터가 없습니다." : "스냅샷 데이터가 아직 없습니다. 첫 실행 후 확인하세요."}
        </div>
      )}

      {/* 테이블 */}
      {filtered.length > 0 && (
        <div className="overflow-x-auto rounded border border-[var(--border)]">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] bg-[var(--surface)]">
                <th className="px-4 py-2 text-left text-[var(--text-muted)]">개념</th>
                <th className="px-4 py-2 text-left text-[var(--text-muted)]">유형</th>
                <th className="px-4 py-2 text-left text-[var(--text-muted)]">추이</th>
                <th className="px-4 py-2 text-right text-[var(--text-muted)]">성장률</th>
                <th className="px-4 py-2 text-left text-[var(--text-muted)]">12주 스파크라인</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((node) => (
                <tr key={node.id} className="border-b border-[var(--border)] hover:bg-[var(--surface-hover)]">
                  <td className="px-4 py-2 text-[var(--text-primary)]">{node.concept_name}</td>
                  <td className="px-4 py-2 text-[var(--text-muted)]">{node.concept_type}</td>
                  <td className="px-4 py-2">
                    <span style={{ color: TREND_COLORS[node.trend] }}>{TREND_LABELS[node.trend]}</span>
                  </td>
                  <td className="px-4 py-2 text-right" style={{ color: TREND_COLORS[node.trend] }}>
                    {node.growth_rate != null
                      ? `${node.growth_rate > 0 ? "+" : ""}${(node.growth_rate * 100).toFixed(1)}%`
                      : "—"}
                  </td>
                  <td className="px-4 py-2">
                    <Sparkline counts={node.weekly_counts} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 커밋**

```bash
git add admin-dashboard/app/analytics/knowledge-trends/page.tsx
git commit -m "feat(knowledge): add /analytics/knowledge-trends page"
```

---

## Task 13: 사이드바 네비게이션 추가

**Files:**
- Modify: `admin-dashboard/components/sidebar.tsx`

- [ ] **Step 1: NAV_ITEMS에 2개 항목 추가**

`admin-dashboard/components/sidebar.tsx`에서 `{ href: "/analytics/ui-split", label: "UI 분석", icon: "🔀" }` 바로 아래에 추가:

```typescript
  { href: "/analytics/knowledge-graph", label: "지식 그래프", icon: "🧠" },
  { href: "/analytics/knowledge-trends", label: "지식 추이", icon: "📡" },
```

- [ ] **Step 2: dev 서버 실행 후 사이드바 확인**

```bash
cd admin-dashboard
npm run dev
```

브라우저에서 `http://localhost:3000` 열어 사이드바에 "지식 그래프"와 "지식 추이" 항목이 보이는지 확인.

- [ ] **Step 3: /analytics/knowledge-graph 페이지 접속 확인**

`http://localhost:3000/analytics/knowledge-graph` 접속.
Expected: 페이지 렌더링, "아직 추출된 지식 개념이 없습니다" 메시지 표시.

- [ ] **Step 4: /analytics/knowledge-trends 페이지 접속 확인**

`http://localhost:3000/analytics/knowledge-trends` 접속.
Expected: 페이지 렌더링, 스냅샷 없음 메시지 표시.

- [ ] **Step 5: 빌드 확인**

```bash
npm run build
```

Expected: `Route /analytics/knowledge-graph` 와 `/analytics/knowledge-trends` 포함, 에러 없음.

- [ ] **Step 6: 커밋**

```bash
git add admin-dashboard/components/sidebar.tsx
git commit -m "feat(knowledge): add knowledge graph & trends nav links to sidebar"
```

---

## Phase 1 완료 기준

- [ ] `alembic upgrade head` 실행 시 7개 테이블 + `knowledge_extracted_at` 컬럼 생성
- [ ] `python -m pytest tests/test_knowledge_extractor.py tests/test_knowledge_router.py -v` 전체 통과
- [ ] `GET /api/v1/knowledge/graph` — 200 응답, `{nodes, edges, total_nodes, total_edges}` 반환
- [ ] `GET /api/v1/knowledge/trends` — 200 응답, `{nodes, period_weeks}` 반환
- [ ] `/analytics/knowledge-graph` 페이지 렌더링 (React Flow 그래프 표시)
- [ ] `/analytics/knowledge-trends` 페이지 렌더링 (추이 테이블 표시)
- [ ] `npm run build` 에러 없음

---

## 주의사항

1. **Bedrock 자격증명**: `knowledge_extractor.py`의 `_call_haiku()`는 환경 변수의 AWS 자격증명을 사용한다. 로컬 개발 시 `aws configure` 또는 `AWS_PROFILE` 설정 필요.
2. **첫 실행 전 데이터**: `prompt_audit_conversations`에 `knowledge_extracted_at IS NULL` 데이터가 있어야 추출이 동작한다. 기존 모든 데이터가 첫 실행 대상.
3. **pgvector**: Phase 1에서는 `embedding` 컬럼을 사용하지 않는다. Phase 2에서 필요 시 별도 마이그레이션으로 추가.
4. **ReactFlow CSS**: `@xyflow/react/dist/style.css` import가 `KnowledgeGraph.tsx`에 포함됨. Next.js global CSS와 충돌 없음.
