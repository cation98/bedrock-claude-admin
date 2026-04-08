"""Tests for shared-mounts auth and file_path traversal prevention.

Covers:
  1. GET /api/v1/files/shared-mounts/{username} authentication
     - Unauthenticated → 401/403
     - Own user accessing their mounts → 200
     - Admin accessing any user's mounts → 200
     - Other user accessing another user's mounts → 403

  2. POST /api/v1/files/datasets file_path validation
     - Path traversal (../../etc/passwd) → 400
     - Absolute path (/etc/passwd) → 400
     - Valid relative path (uploads/data.csv) → 201 accepted
     - Normalized path (uploads/./data.csv) → stored as uploads/data.csv
"""

import json
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator, Text
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.core.config import Settings, get_settings
from app.core.security import get_current_user_or_pod

# Register all models needed for FK resolution and table creation
from app.models.user import User  # noqa: F401
from app.models.file_share import SharedDataset, FileShareACL  # noqa: F401
from app.models.session import TerminalSession  # noqa: F401

from app.routers import file_share as file_share_router

from sqlalchemy.dialects.postgresql import JSONB


# --------------- SQLite JSONB compatibility ---------------

class _JSONBtoText(TypeDecorator):
    """Store JSONB values as TEXT in SQLite with transparent JSON serde."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if isinstance(value, str):
                return value
            return json.dumps(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            if isinstance(value, str):
                return json.loads(value)
            return value
        return value


for _table in Base.metadata.tables.values():
    for _col in _table.columns:
        if isinstance(_col.type, JSONB):
            _col.type = _JSONBtoText()


# --------------- Test DB ---------------

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


# --------------- Test Settings ---------------

def _test_settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        debug=False,
    )


# --------------- Build minimal test app ---------------

def _build_test_app() -> FastAPI:
    app = FastAPI(title="Test FileShare")
    app.include_router(file_share_router.router)
    return app


_test_app = _build_test_app()


# --------------- Fixtures ---------------

@pytest.fixture(autouse=True)
def _setup_tables():
    Base.metadata.create_all(bind=_test_engine)
    yield
    Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture()
def db_session():
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    """TestClient with DB and settings overrides but NO auth override.

    Individual tests apply their own auth override via override_current_user.
    """
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    _test_app.dependency_overrides[get_db] = _override_get_db
    _test_app.dependency_overrides[get_settings] = _test_settings

    with TestClient(_test_app, raise_server_exceptions=False) as tc:
        yield tc

    _test_app.dependency_overrides.clear()


def override_current_user(user: dict):
    """Return a dependency override that returns the given user dict."""
    def _dep():
        return user
    return _dep


# --------------- Helpers ---------------

def _create_user(db, username: str, role: str = "user") -> User:
    user = User(username=username, name=username, role=role, is_approved=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_dataset(db, owner_username: str, dataset_name: str = "test-ds",
                    file_path: str = "shared-data/erp.sqlite") -> SharedDataset:
    ds = SharedDataset(
        owner_username=owner_username,
        dataset_name=dataset_name,
        file_path=file_path,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


def _create_acl(db, dataset_id: int, share_type: str, share_target: str,
                granted_by: str) -> FileShareACL:
    acl = FileShareACL(
        dataset_id=dataset_id,
        share_type=share_type,
        share_target=share_target,
        granted_by=granted_by,
    )
    db.add(acl)
    db.commit()
    db.refresh(acl)
    return acl


# ==================== shared-mounts auth tests ====================


def test_shared_mounts_unauthenticated(client):
    """No auth → 401 or 403 (authentication required)."""
    # No override_current_user set — get_current_user_or_pod is NOT overridden,
    # but TestClient has raise_server_exceptions=False and no real token,
    # so we mock the dependency to raise 403 (no token supplied).
    # With no override, the real get_current_user_or_pod runs but has no token → 403.
    resp = client.get("/api/v1/files/shared-mounts/SOMEUSER")
    assert resp.status_code in (401, 403)


def test_shared_mounts_own_user(client, db_session):
    """User accessing their own mounts → 200."""
    _create_user(db_session, "USER01")
    owner = _create_user(db_session, "OWNER01")
    ds = _create_dataset(db_session, owner_username="OWNER01")
    _create_acl(db_session, ds.id, "user", "USER01", "OWNER01")

    _test_app.dependency_overrides[get_current_user_or_pod] = override_current_user(
        {"sub": "USER01", "role": "user"}
    )

    resp = client.get("/api/v1/files/shared-mounts/USER01")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["dataset_name"] == "test-ds"

    _test_app.dependency_overrides.pop(get_current_user_or_pod, None)


def test_shared_mounts_admin(client, db_session):
    """Admin accessing any user's mounts → 200."""
    _create_user(db_session, "USER02")
    owner = _create_user(db_session, "OWNER02")
    ds = _create_dataset(db_session, owner_username="OWNER02", dataset_name="admin-view-ds")
    _create_acl(db_session, ds.id, "user", "USER02", "OWNER02")

    _test_app.dependency_overrides[get_current_user_or_pod] = override_current_user(
        {"sub": "ADMINUSER", "role": "admin"}
    )

    resp = client.get("/api/v1/files/shared-mounts/USER02")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["dataset_name"] == "admin-view-ds"

    _test_app.dependency_overrides.pop(get_current_user_or_pod, None)


def test_shared_mounts_other_user(client, db_session):
    """User accessing another user's mounts → 403."""
    _test_app.dependency_overrides[get_current_user_or_pod] = override_current_user(
        {"sub": "USER_A", "role": "user"}
    )

    resp = client.get("/api/v1/files/shared-mounts/USER_B")
    assert resp.status_code == 403

    _test_app.dependency_overrides.pop(get_current_user_or_pod, None)


# ==================== file_path validation tests ====================


def _post_dataset(client, file_path: str, dataset_name: str = "test-ds") -> object:
    """Helper to POST /api/v1/files/datasets with the given file_path."""
    return client.post("/api/v1/files/datasets", json={
        "dataset_name": dataset_name,
        "file_path": file_path,
        "file_type": "csv",
        "file_size_bytes": 100,
    })


def test_file_path_traversal_rejected(client, db_session):
    """Path traversal (../../etc/passwd) → 400."""
    _create_user(db_session, "TESTUSER01")
    _test_app.dependency_overrides[get_current_user_or_pod] = override_current_user(
        {"sub": "TESTUSER01", "role": "user"}
    )

    resp = _post_dataset(client, "../../etc/passwd", "traversal-ds")
    assert resp.status_code == 400

    _test_app.dependency_overrides.pop(get_current_user_or_pod, None)


def test_file_path_absolute_rejected(client, db_session):
    """Absolute path (/etc/passwd) → 400."""
    _create_user(db_session, "TESTUSER01")
    _test_app.dependency_overrides[get_current_user_or_pod] = override_current_user(
        {"sub": "TESTUSER01", "role": "user"}
    )

    resp = _post_dataset(client, "/etc/passwd", "abs-ds")
    assert resp.status_code == 400

    _test_app.dependency_overrides.pop(get_current_user_or_pod, None)


def test_file_path_valid(client, db_session):
    """Valid relative path (uploads/data.csv) → 201 created."""
    _create_user(db_session, "TESTUSER01")
    _test_app.dependency_overrides[get_current_user_or_pod] = override_current_user(
        {"sub": "TESTUSER01", "role": "user"}
    )

    resp = _post_dataset(client, "uploads/data.csv", "valid-ds")
    assert resp.status_code == 201
    assert resp.json()["file_path"] == "uploads/data.csv"

    _test_app.dependency_overrides.pop(get_current_user_or_pod, None)


def test_file_path_normalized(client, db_session):
    """Path with redundant dots (uploads/./data.csv) → stored as uploads/data.csv."""
    _create_user(db_session, "TESTUSER01")
    _test_app.dependency_overrides[get_current_user_or_pod] = override_current_user(
        {"sub": "TESTUSER01", "role": "user"}
    )

    resp = _post_dataset(client, "uploads/./data.csv", "normalized-ds")
    assert resp.status_code == 201
    assert resp.json()["file_path"] == "uploads/data.csv"

    _test_app.dependency_overrides.pop(get_current_user_or_pod, None)
