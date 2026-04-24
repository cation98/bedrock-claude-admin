import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator, Text

from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.knowledge import KnowledgeNode, KnowledgeEdge, KnowledgeMention, KnowledgeSnapshot, WorkflowTemplate, KnowledgeTaxonomy  # noqa: F401
from app.models.prompt_audit import PromptAuditConversation, PromptAuditSummary, PromptAuditFlag  # noqa: F401
from app.routers.knowledge import router


# SQLite in-memory test DB
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
    edge = KnowledgeEdge(
        source_node_id=n1.id,
        target_node_id=n2.id,
        edge_type="co_occurs",
        weight=2.0,
        co_occurrence_count=2,
    )
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


# ── Phase 2 분석 엔드포인트 테스트 ────────────────────────────────

def test_get_associations_empty(client):
    res = client.get("/api/v1/knowledge/associations")
    assert res.status_code == 200
    data = res.json()
    assert data["rules"] == []
    assert data["total"] == 0


def test_get_associations_returns_rules(client, db):
    n1 = KnowledgeNode(concept_name="A", concept_type="tool", normalized_name="a2")
    n2 = KnowledgeNode(concept_name="B", concept_type="skill", normalized_name="b2")
    db.add_all([n1, n2])
    db.flush()
    for i in range(10):
        db.add(KnowledgeMention(conversation_id=100 + i, node_id=n1.id, username="U1"))
    for i in range(8):
        db.add(KnowledgeMention(conversation_id=200 + i, node_id=n2.id, username="U2"))
    db.add(KnowledgeEdge(source_node_id=n1.id, target_node_id=n2.id,
                         edge_type="co_occurs", co_occurrence_count=8))
    db.commit()
    # lift = 8*18/(10*8) = 1.8 >= 1.5
    res = client.get("/api/v1/knowledge/associations?min_support=0.0&min_lift=1.5")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1


def test_get_departments_empty(client):
    res = client.get("/api/v1/knowledge/departments")
    assert res.status_code == 200
    data = res.json()
    assert data["departments"] == []
    assert data["nodes"] == []


def test_get_gap_returns_404_for_missing_template(client):
    res = client.get("/api/v1/knowledge/gap?template_id=9999")
    assert res.status_code == 404


def test_get_gap_returns_report(client, db):
    import json as _json
    node = KnowledgeNode(concept_name="PyTest", concept_type="tool", normalized_name="pytest2")
    db.add(node)
    db.flush()
    tmpl = WorkflowTemplate(
        name="Test Wf",
        steps=_json.dumps([{"id": "s1", "name": "테스트"}]),
        connections=_json.dumps([]),
    )
    db.add(tmpl)
    db.flush()
    db.add(KnowledgeTaxonomy(knowledge_node_id=node.id, workflow_template_id=tmpl.id,
                              workflow_step_id="s1"))
    db.commit()
    res = client.get(f"/api/v1/knowledge/gap?template_id={tmpl.id}")
    assert res.status_code == 200
    data = res.json()
    assert data["template_name"] == "Test Wf"
    assert "coverage_rate" in data
