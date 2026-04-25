import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator, Text

from app.core.database import Base
from app.models.knowledge import (  # noqa: F401
    KnowledgeNode, KnowledgeEdge, KnowledgeMention,
    KnowledgeSnapshot, WorkflowTemplate, KnowledgeTaxonomy,
)
from app.models.prompt_audit import PromptAuditConversation, PromptAuditSummary, PromptAuditFlag  # noqa: F401

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


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


def test_compute_associations_empty_returns_empty(db):
    from app.services.knowledge_analyzer import compute_associations
    assert compute_associations(db) == []


def test_compute_associations_calculates_lift(db):
    from app.services.knowledge_analyzer import compute_associations
    n1 = KnowledgeNode(concept_name="A", concept_type="tool", normalized_name="a")
    n2 = KnowledgeNode(concept_name="B", concept_type="skill", normalized_name="b")
    db.add_all([n1, n2])
    db.flush()

    # 10 total mentions: 8 for n1, 6 for n2
    for i in range(8):
        db.add(KnowledgeMention(conversation_id=i + 1, node_id=n1.id, username="U1"))
    for i in range(6):
        db.add(KnowledgeMention(conversation_id=i + 1, node_id=n2.id, username="U2"))

    edge = KnowledgeEdge(source_node_id=n1.id, target_node_id=n2.id,
                         edge_type="co_occurs", co_occurrence_count=4)
    db.add(edge)
    db.commit()

    # total_mentions=14, co=4, src_cnt=8, tgt_cnt=6
    # support = 4/14 ≈ 0.2857
    # confidence = 4/8 = 0.5
    # lift = 4*14 / (8*6) ≈ 1.1667
    results = compute_associations(db, min_support=0.0, min_lift=1.0)
    assert len(results) == 1
    r = results[0]
    assert r["source_node_id"] == n1.id
    assert r["confidence"] == pytest.approx(0.5, rel=1e-3)
    assert r["lift"] == pytest.approx(56 / 48, rel=1e-3)


def test_compute_associations_filters_by_min_lift(db):
    from app.services.knowledge_analyzer import compute_associations
    n1 = KnowledgeNode(concept_name="A", concept_type="tool", normalized_name="a")
    n2 = KnowledgeNode(concept_name="B", concept_type="skill", normalized_name="b")
    db.add_all([n1, n2])
    db.flush()
    for i in range(8):
        db.add(KnowledgeMention(conversation_id=i + 1, node_id=n1.id, username="U1"))
    for i in range(6):
        db.add(KnowledgeMention(conversation_id=i + 1, node_id=n2.id, username="U2"))
    db.add(KnowledgeEdge(source_node_id=n1.id, target_node_id=n2.id,
                         edge_type="co_occurs", co_occurrence_count=4))
    db.commit()
    # lift ≈ 1.17 < 1.5, should be filtered
    results = compute_associations(db, min_support=0.0, min_lift=1.5)
    assert results == []


def test_compute_department_stats_empty(db):
    from app.services.knowledge_analyzer import compute_department_stats
    result = compute_department_stats(db)
    assert result["departments"] == []
    assert result["nodes"] == []


def test_compute_gap_analysis_returns_none_for_missing_template(db):
    from app.services.knowledge_analyzer import compute_gap_analysis
    assert compute_gap_analysis(db, template_id=999) is None


def test_compute_gap_analysis_coverage_and_undocumented(db):
    from app.services.knowledge_analyzer import compute_gap_analysis
    n1 = KnowledgeNode(concept_name="Python", concept_type="tool", normalized_name="python")
    n2 = KnowledgeNode(concept_name="Docker", concept_type="tool", normalized_name="docker")
    db.add_all([n1, n2])
    db.flush()

    tmpl = WorkflowTemplate(
        name="Dev Workflow",
        steps=json.dumps([{"id": "s1", "name": "개발"}]),
        connections=json.dumps([]),
    )
    db.add(tmpl)
    db.flush()

    # n1 mapped to template, n2 not
    db.add(KnowledgeTaxonomy(
        knowledge_node_id=n1.id,
        workflow_template_id=tmpl.id,
        workflow_step_id="s1",
    ))
    # n2 has mentions
    db.add(KnowledgeMention(conversation_id=1, node_id=n2.id, username="U1"))
    db.commit()

    result = compute_gap_analysis(db, template_id=tmpl.id)
    assert result is not None
    assert result["template_name"] == "Dev Workflow"
    # 1 of 2 active nodes mapped → coverage = 0.5
    assert result["coverage_rate"] == pytest.approx(0.5, rel=1e-3)
    # n2 is undocumented (not mapped, has mention)
    assert len(result["undocumented_knowledge"]) == 1
    assert result["undocumented_knowledge"][0]["node_id"] == n2.id
