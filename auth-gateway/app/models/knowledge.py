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
        UniqueConstraint("source_node_id", "target_node_id", "edge_type", name="uq_edge_nodes_type"),
    )


class KnowledgeMention(Base):
    __tablename__ = "knowledge_mentions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False)
    node_id = Column(Integer, nullable=False, index=True)
    username = Column(String(100), nullable=False, index=True)
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
        UniqueConstraint("snapshot_date", "granularity", "node_id", name="uq_snapshot_date_gran_node"),
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
        UniqueConstraint("knowledge_node_id", "workflow_template_id", "workflow_step_id", name="uq_taxonomy_mapping"),
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
