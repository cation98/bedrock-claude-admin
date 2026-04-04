"""프록시 관리 Admin API 테스트.

허용 도메인 CRUD + 프록시 접근 로그 조회 엔드포인트를 검증.
기존 conftest.py의 TestClient/DB 패턴을 재사용.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.core.config import Settings, get_settings
from app.core.security import get_current_user
from app.models.proxy import AllowedDomain, ProxyAccessLog  # noqa: F401
from app.routers import admin as admin_router


# --------------- Test DB Setup ---------------

_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_test_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_TestSession = sessionmaker(bind=_test_engine)


def _test_settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        debug=False,
    )


_ADMIN_USER = {"sub": "ADMIN01", "role": "admin", "name": "Admin User"}


def _mock_admin_user() -> dict:
    return _ADMIN_USER.copy()


# Build a minimal test app with just admin router
_test_app = FastAPI(title="Test Proxy Admin")
_test_app.include_router(admin_router.router)


@pytest.fixture(autouse=True)
def _setup_tables():
    Base.metadata.create_all(bind=_test_engine)
    yield
    Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture()
def db():
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    def _override_get_db():
        try:
            yield db
        finally:
            pass

    _test_app.dependency_overrides[get_db] = _override_get_db
    _test_app.dependency_overrides[get_settings] = _test_settings
    _test_app.dependency_overrides[get_current_user] = _mock_admin_user

    with TestClient(_test_app, raise_server_exceptions=False) as tc:
        yield tc

    _test_app.dependency_overrides.clear()


# --------------- Tests: Allowed Domains ---------------


def test_get_allowed_domains_returns_list(client, db):
    """GET /admin/allowed-domains — 도메인 목록 반환."""
    db.add(AllowedDomain(domain="apis.data.go.kr", is_wildcard=False, enabled=True))
    db.add(AllowedDomain(domain="*.amazonaws.com", is_wildcard=True, enabled=True))
    db.commit()

    resp = client.get("/api/v1/admin/allowed-domains")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["domains"]) == 2


def test_post_adds_domain(client):
    """POST /admin/allowed-domains — 도메인 추가."""
    resp = client.post("/api/v1/admin/allowed-domains", json={
        "domain": "new.example.com",
        "description": "Test domain",
        "is_wildcard": False,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["domain"] == "new.example.com"
    assert data["enabled"] is True
    assert data["created_by"] == "ADMIN01"


def test_post_duplicate_returns_409(client):
    """POST 중복 도메인 → 409 Conflict."""
    client.post("/api/v1/admin/allowed-domains", json={
        "domain": "apis.data.go.kr",
    })
    resp = client.post("/api/v1/admin/allowed-domains", json={
        "domain": "apis.data.go.kr",
    })
    assert resp.status_code == 409


def test_delete_removes_domain(client):
    """DELETE /admin/allowed-domains/{id} — 도메인 삭제."""
    resp = client.post("/api/v1/admin/allowed-domains", json={
        "domain": "to-delete.com",
    })
    domain_id = resp.json()["id"]

    resp = client.delete(f"/api/v1/admin/allowed-domains/{domain_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # 목록에서 사라졌는지 확인
    resp = client.get("/api/v1/admin/allowed-domains")
    assert len(resp.json()["domains"]) == 0


def test_patch_toggles_enabled(client):
    """PATCH /admin/allowed-domains/{id} — 활성/비활성 토글."""
    resp = client.post("/api/v1/admin/allowed-domains", json={
        "domain": "toggle.example.com",
    })
    domain_id = resp.json()["id"]

    # 비활성화
    resp = client.patch(f"/api/v1/admin/allowed-domains/{domain_id}", json={
        "enabled": False,
    })
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # 다시 활성화
    resp = client.patch(f"/api/v1/admin/allowed-domains/{domain_id}", json={
        "enabled": True,
    })
    assert resp.json()["enabled"] is True


# --------------- Tests: Proxy Logs ---------------


def test_get_proxy_logs_with_pagination(client, db):
    """GET /admin/proxy-logs — 페이지네이션 + 필터."""
    from datetime import datetime, timezone

    # 테스트 로그 삽입
    for i in range(5):
        db.add(ProxyAccessLog(
            user_id="USER01",
            domain=f"api{i}.example.com",
            method="CONNECT",
            allowed=True,
            response_time_ms=10 + i,
            created_at=datetime.now(timezone.utc),
        ))
    db.add(ProxyAccessLog(
        user_id="USER02",
        domain="blocked.com",
        method="CONNECT",
        allowed=False,
        response_time_ms=5,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    # 전체 조회
    resp = client.get("/api/v1/admin/proxy-logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 6
    assert len(data["logs"]) == 6

    # user_id 필터
    resp = client.get("/api/v1/admin/proxy-logs?user_id=USER02")
    data = resp.json()
    assert data["total"] == 1
    assert data["logs"][0]["user_id"] == "USER02"

    # domain 필터
    resp = client.get("/api/v1/admin/proxy-logs?domain=blocked")
    data = resp.json()
    assert data["total"] == 1
    assert data["logs"][0]["domain"] == "blocked.com"

    # 페이지네이션
    resp = client.get("/api/v1/admin/proxy-logs?skip=0&limit=2")
    data = resp.json()
    assert len(data["logs"]) == 2
    assert data["total"] == 6
