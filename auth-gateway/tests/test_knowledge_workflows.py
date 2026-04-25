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
from app.models.knowledge import (  # noqa: F401
    KnowledgeNode, KnowledgeTaxonomy, WorkflowTemplate,
)
from app.models.prompt_audit import PromptAuditConversation, PromptAuditSummary, PromptAuditFlag  # noqa: F401
from app.routers.knowledge import router

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


def test_create_workflow_template(client):
    payload = {
        "name": "개발 워크플로우",
        "description": "테스트",
        "steps": [{"id": "s1", "name": "분석"}],
        "connections": [],
    }
    res = client.post("/api/v1/knowledge/workflows", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "개발 워크플로우"
    assert data["id"] > 0


def test_list_workflow_templates(client, db):
    db.add(WorkflowTemplate(name="WF1", steps=json.dumps([]), connections=json.dumps([])))
    db.add(WorkflowTemplate(name="WF2", steps=json.dumps([]), connections=json.dumps([])))
    db.commit()
    res = client.get("/api/v1/knowledge/workflows")
    assert res.status_code == 200
    assert len(res.json()) == 2


def test_get_workflow_template_not_found(client):
    res = client.get("/api/v1/knowledge/workflows/9999")
    assert res.status_code == 404


def test_update_workflow_template(client, db):
    tmpl = WorkflowTemplate(name="Old", steps=json.dumps([]), connections=json.dumps([]))
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    res = client.put(f"/api/v1/knowledge/workflows/{tmpl.id}", json={"name": "New"})
    assert res.status_code == 200
    assert res.json()["name"] == "New"


def test_delete_workflow_template(client, db):
    tmpl = WorkflowTemplate(name="ToDelete", steps=json.dumps([]), connections=json.dumps([]))
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    res = client.delete(f"/api/v1/knowledge/workflows/{tmpl.id}")
    assert res.status_code == 204
    assert db.query(WorkflowTemplate).filter_by(id=tmpl.id).first() is None


def test_create_taxonomy_mapping(client, db):
    node = KnowledgeNode(concept_name="Python", concept_type="tool", normalized_name="python3")
    tmpl = WorkflowTemplate(name="WF", steps=json.dumps([{"id": "s1", "name": "개발"}]),
                             connections=json.dumps([]))
    db.add_all([node, tmpl])
    db.commit()
    db.refresh(node)
    db.refresh(tmpl)
    res = client.post("/api/v1/knowledge/taxonomy", json={
        "knowledge_node_id": node.id,
        "workflow_template_id": tmpl.id,
        "workflow_step_id": "s1",
    })
    assert res.status_code == 201
    assert res.json()["workflow_step_id"] == "s1"


def test_list_taxonomy_by_template(client, db):
    node = KnowledgeNode(concept_name="Docker", concept_type="tool", normalized_name="docker2")
    tmpl = WorkflowTemplate(name="WF2", steps=json.dumps([]), connections=json.dumps([]))
    db.add_all([node, tmpl])
    db.flush()
    db.add(KnowledgeTaxonomy(knowledge_node_id=node.id, workflow_template_id=tmpl.id,
                              workflow_step_id="s1"))
    db.commit()
    db.refresh(tmpl)
    res = client.get(f"/api/v1/knowledge/taxonomy?template_id={tmpl.id}")
    assert res.status_code == 200
    assert len(res.json()) == 1
