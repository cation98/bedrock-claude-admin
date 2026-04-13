"""Phase 1a: /docs, /redoc, /openapi.json 공개 차단 회귀 방지."""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_docs_returns_404():
    resp = client.get("/docs")
    assert resp.status_code == 404, f"/docs should be hidden, got {resp.status_code}"


def test_redoc_returns_404():
    resp = client.get("/redoc")
    assert resp.status_code == 404


def test_openapi_json_returns_404():
    resp = client.get("/openapi.json")
    assert resp.status_code == 404
