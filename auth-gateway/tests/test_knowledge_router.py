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
from app.models.knowledge import KnowledgeNode, KnowledgeEdge, KnowledgeMention  # noqa: F401
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
