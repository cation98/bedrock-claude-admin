"""Shared pytest fixtures for auth-gateway tests.

Uses SQLite in-memory DB (with StaticPool to share across connections)
to isolate tests from production PostgreSQL. Builds a lightweight FastAPI
app with only the routers under test, avoiding the real lifespan which
connects to PostgreSQL and starts background schedulers.
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

# Import ALL models that may be queried during tests so Base.metadata
# registers their tables for create_all.
from app.models.app import DeployedApp, AppACL, AppView  # noqa: F401
from app.models.user import User  # noqa: F401

# Import routers under test
from app.routers import apps as apps_router
from app.routers import app_proxy as app_proxy_router


# --------------- Test DB (SQLite in-memory, shared across connections) ---------------

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


# SQLite does not enforce FK constraints by default — enable them.
@event.listens_for(test_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestSessionLocal = sessionmaker(bind=test_engine)


# --------------- Test Settings ---------------

def _test_settings() -> Settings:
    """Return Settings with a deterministic JWT secret and SQLite URL."""
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        debug=False,
    )


# --------------- Mock current user ---------------

_DEFAULT_TEST_USER = {
    "sub": "TESTUSER01",
    "role": "user",
    "name": "Test User",
}


def _mock_current_user() -> dict:
    return _DEFAULT_TEST_USER.copy()


# --------------- Build a lightweight test app ---------------

def _build_test_app() -> FastAPI:
    """Create a minimal FastAPI app with only the routers needed for tests.

    This avoids importing app.main which triggers the real lifespan
    (PostgreSQL connection, migrations, background schedulers).
    """
    test_app = FastAPI(title="Test Auth Gateway")
    test_app.include_router(apps_router.router)
    test_app.include_router(app_proxy_router.router)
    return test_app


_test_app = _build_test_app()


# --------------- Fixtures ---------------

@pytest.fixture(autouse=True)
def _setup_tables():
    """Create all tables before each test and drop them after."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def db_session():
    """Yield a fresh SQLAlchemy session, rolled back after the test."""
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    """FastAPI TestClient with dependency overrides for DB, settings, and auth."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass  # session closed by db_session fixture

    _test_app.dependency_overrides[get_db] = _override_get_db
    _test_app.dependency_overrides[get_settings] = _test_settings
    _test_app.dependency_overrides[get_current_user] = _mock_current_user

    with TestClient(_test_app, raise_server_exceptions=False) as tc:
        yield tc

    _test_app.dependency_overrides.clear()


@pytest.fixture()
def test_settings() -> Settings:
    return _test_settings()


# --------------- Helper fixtures ---------------

@pytest.fixture()
def create_test_user(db_session):
    """Factory fixture: insert a User row and return it."""

    def _create(
        username: str = "TESTUSER01",
        name: str = "Test User",
        role: str = "user",
        is_approved: bool = True,
        can_deploy_apps: bool = True,
    ) -> User:
        user = User(
            username=username,
            name=name,
            role=role,
            is_approved=is_approved,
            can_deploy_apps=can_deploy_apps,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _create


@pytest.fixture()
def create_test_app(db_session):
    """Factory fixture: insert a DeployedApp row and return it."""

    def _create(
        owner_username: str = "TESTUSER01",
        app_name: str = "my-app",
        status: str = "running",
        visibility: str = "private",
        app_port: int = 3000,
        version: str = "v1",
    ) -> DeployedApp:
        deployed = DeployedApp(
            owner_username=owner_username,
            app_name=app_name,
            app_url=f"/apps/{owner_username}/{app_name}/",
            pod_name=f"app-{owner_username.lower()}-{app_name}",
            status=status,
            version=version,
            visibility=visibility,
            app_port=app_port,
        )
        db_session.add(deployed)
        db_session.commit()
        db_session.refresh(deployed)
        return deployed

    return _create
