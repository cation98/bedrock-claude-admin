"""Shared pytest fixtures for auth-gateway tests.

Uses SQLite in-memory DB (with StaticPool to share across connections)
to isolate tests from production PostgreSQL. Builds a lightweight FastAPI
app with only the routers under test, avoiding the real lifespan which
connects to PostgreSQL and starts background schedulers.
"""

import json
import os

# ONLYOFFICE_JWT_SECRET은 Settings에서 필수 필드이므로, 개별 test_*.py 파일이
# Settings()를 직접 인스턴스화할 때 env var가 없으면 실패한다. 테스트 부팅 시점에
# placeholder가 아닌 dummy 값을 주입한다.
os.environ.setdefault(
    "ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-32-chars-min-xx"
)

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator, Text
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.core.config import Settings, get_settings
from app.core.security import get_current_user, get_current_user_or_pod

# Import ALL models that may be queried during tests so Base.metadata
# registers their tables for create_all.
from app.models.app import DeployedApp, AppACL, AppView  # noqa: F401
from app.models.bot import UserBot  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.session import TerminalSession  # noqa: F401
from app.models.survey import SurveyTemplate, SurveyAssignment, SurveyResponse  # noqa: F401
from app.models.proxy import AllowedDomain, ProxyAccessLog  # noqa: F401
from app.models.file_governance import GovernedFile  # noqa: F401
from app.models.file_audit import FileAuditLog  # noqa: F401
from app.models.file_share import SharedDataset, FileShareACL  # noqa: F401
from app.models.two_factor_code import TwoFactorCode  # noqa: F401
from app.models.knowledge import (  # noqa: F401
    KnowledgeNode, KnowledgeEdge, KnowledgeMention,
    KnowledgeSnapshot, WorkflowTemplate, KnowledgeTaxonomy, WorkflowInstance,
)
from app.routers.telegram import TelegramMapping, TelegramChatLog  # noqa: F401
from app.routers.sms import SmsLog  # noqa: F401
from app.services.sqlcipher_service import SQLCipherKey  # noqa: F401

# Import routers under test
from app.routers import apps as apps_router
from app.routers import app_proxy as app_proxy_router
from app.routers import bots as bots_router
from app.routers import surveys as surveys_router
from app.routers import file_governance as file_governance_router
from app.routers import file_share as file_share_router
from app.routers import skills as skills_router
from app.routers import sms as sms_router


# --------------- SQLite JSONB compatibility ---------------

# PostgreSQL JSONB columns fail on SQLite. We replace all JSONB column
# types in model metadata with a TypeDecorator that stores as TEXT and
# transparently serializes/deserializes JSON. This ensures both DDL
# (CREATE TABLE) and value round-tripping work correctly.

from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402


class _JSONBtoText(TypeDecorator):
    """Store JSONB values as TEXT in SQLite with transparent JSON serde."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if isinstance(value, str):
                return value  # already serialized
            return json.dumps(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            if isinstance(value, str):
                return json.loads(value)
            return value  # already deserialized
        return value


# Swap JSONB column types to _JSONBtoText for all registered models.
# This must run after all models are imported and before create_all.
for _table in Base.metadata.tables.values():
    for _col in _table.columns:
        if isinstance(_col.type, JSONB):
            _col.type = _JSONBtoText()


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
        onlyoffice_jwt_secret="test-onlyoffice-jwt-secret-32-chars-min-xx",
        sms_gateway_url="http://fake-sms-gateway.test/send",
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
    test_app.include_router(bots_router.router)
    test_app.include_router(app_proxy_router.router)
    test_app.include_router(surveys_router.router)
    test_app.include_router(file_governance_router.router)
    test_app.include_router(file_share_router.router)
    test_app.include_router(skills_router.router)
    test_app.include_router(sms_router.router)
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
    _test_app.dependency_overrides[get_current_user_or_pod] = _mock_current_user

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
        can_send_sms: bool = True,  # 테스트 기본값 True — 회귀 방지
    ) -> User:
        user = User(
            username=username,
            name=name,
            role=role,
            is_approved=is_approved,
            can_deploy_apps=can_deploy_apps,
            can_send_sms=can_send_sms,
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


# --------------- Survey helper fixtures ---------------

@pytest.fixture()
def create_test_survey_template(db_session):
    """Factory fixture: insert a SurveyTemplate row and return it."""

    def _create(
        owner_username: str = "TESTUSER01",
        title: str = "Test Survey",
        description: str = "A test survey",
        questions: list | None = None,
        status: str = "active",
    ) -> SurveyTemplate:
        if questions is None:
            questions = [
                {"type": "text", "label": "Describe the situation", "required": True},
                {"type": "choice", "label": "Status", "options": ["Good", "Bad"], "required": True},
            ]
        template = SurveyTemplate(
            owner_username=owner_username,
            title=title,
            description=description,
            questions=json.dumps(questions) if isinstance(questions, list) else questions,
            status=status,
        )
        db_session.add(template)
        db_session.commit()
        db_session.refresh(template)
        return template

    return _create


@pytest.fixture()
def create_test_assignment(db_session):
    """Factory fixture: insert a SurveyAssignment row and return it."""

    def _create(
        template_id: int,
        target_username: str = "WORKER01",
        telegram_id: str | None = "123456789",
        status: str = "pending",
        current_question_idx: int = 0,
        partial_answers: list | None = None,
    ) -> SurveyAssignment:
        assignment = SurveyAssignment(
            template_id=template_id,
            target_username=target_username,
            telegram_id=telegram_id,
            status=status,
            current_question_idx=current_question_idx,
            partial_answers=json.dumps(partial_answers or []),
        )
        db_session.add(assignment)
        db_session.commit()
        db_session.refresh(assignment)
        return assignment

    return _create


@pytest.fixture()
def create_test_telegram_mapping(db_session):
    """Factory fixture: insert a TelegramMapping row and return it."""

    def _create(
        telegram_id: int = 123456789,
        telegram_name: str = "Test Worker",
        username: str = "WORKER01",
    ) -> TelegramMapping:
        mapping = TelegramMapping(
            telegram_id=telegram_id,
            telegram_name=telegram_name,
            username=username,
        )
        db_session.add(mapping)
        db_session.commit()
        db_session.refresh(mapping)
        return mapping

    return _create


@pytest.fixture()
def override_current_user():
    """테스트 내에서 _mock_current_user가 반환하는 dict를 바꾸는 헬퍼.

    Usage:
        override_current_user(username="TESTUSER", auth_type="jwt")
    """
    from app.core import security as sec_module

    def _set(username: str = "TESTUSER01", auth_type: str = "jwt"):
        def _mock():
            return {
                "sub": username,
                "username": username,
                "role": "user",
                "auth_type": auth_type,
            }
        _test_app.dependency_overrides[sec_module.get_current_user] = _mock
        _test_app.dependency_overrides[sec_module.get_current_user_or_pod] = _mock

    return _set


@pytest.fixture()
def mock_sms_gateway(monkeypatch):
    """외부 SMS 게이트웨이 호출을 차단하고 고정 응답 반환."""
    class _Resp:
        status_code = 200
        def raise_for_status(self):
            pass  # 200 → no-op
        def json(self):
            return {"d": {"Result": {"ResultCode": "1", "ResultMsg": "OK"}}}

    async def _fake_post(self, *args, **kwargs):
        return _Resp()

    monkeypatch.setattr("httpx.AsyncClient.post", _fake_post)
    yield
