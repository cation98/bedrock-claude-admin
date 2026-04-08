"""Security regression tests: TEST* user SSO/2FA bypass.

Verifies that:
1. TEST* usernames cannot skip SSO (bypass is disabled by default).
2. TEST* usernames cannot skip 2FA (bypass is disabled by default).
3. Bypass is functional when ALLOW_TEST_USERS=true (for dev/CI environments).

These tests call the login endpoint logic directly via mocked SSO and 2FA
dependencies so they run without network access.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import Base, get_db
from app.models.user import User
from app.models.audit_log import AuditLog, AuditAction
from app.routers.auth import router as auth_router


# ---------------------------------------------------------------------------
# SQLite in-memory test DB
# ---------------------------------------------------------------------------

_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_Session = sessionmaker(bind=_engine)


@pytest.fixture(autouse=True)
def _setup_tables():
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture()
def db():
    session = _Session()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(allow_test_users: bool = False, two_factor_enabled: bool = True) -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        two_factor_enabled=two_factor_enabled,
        allow_test_users=allow_test_users,
        sso_auth_url="http://fake-sso/auth",
        sso_auth_url2="http://fake-sso/userinfo",
        sms_gateway_url="",  # disabled; won't be reached in bypass tests
    )


def _make_app(settings: Settings, db_session) -> FastAPI:
    """Build a minimal FastAPI app with the auth router and overridden dependencies."""
    from app.core.security import get_current_user

    app = FastAPI()
    app.include_router(auth_router)

    def _override_db():
        try:
            yield db_session
        finally:
            pass

    def _override_settings():
        return settings

    app.dependency_overrides[get_db] = _override_db
    from app.core.config import get_settings
    app.dependency_overrides[get_settings] = _override_settings

    return app


def _insert_test_user(db, username: str = "TESTUSER01", is_approved: bool = True) -> User:
    user = User(
        username=username,
        name="Test User",
        role="user",
        is_approved=is_approved,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Test 1: TEST* user cannot skip SSO (default allow_test_users=False)
# ---------------------------------------------------------------------------

def test_test_user_cannot_skip_sso(db):
    """With allow_test_users=False (default), a TEST* username with
    password 'test2026' must NOT receive a JWT without going through SSO.

    The SSO call will fail (mocked to raise SSOAuthError), and the
    endpoint must return 401 — not bypass to a JWT.
    """
    settings = _make_settings(allow_test_users=False, two_factor_enabled=True)
    _insert_test_user(db, username="TESTUSER01")

    app = _make_app(settings, db)

    from app.services.sso_service import SSOAuthError

    with patch(
        "app.routers.auth.SSOService.authenticate",
        new_callable=AsyncMock,
        side_effect=SSOAuthError("SSO credential rejected"),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "TESTUSER01", "password": "test2026"},
            )

    # Must not succeed — SSO is required
    assert resp.status_code == 401, (
        f"Expected 401 (SSO required) but got {resp.status_code}: {resp.text}"
    )
    # No JWT in the response
    body = resp.json()
    assert "access_token" not in body


def test_test_user_wrong_password_cannot_skip_sso(db):
    """Even if allow_test_users were enabled, wrong password should not bypass SSO."""
    settings = _make_settings(allow_test_users=False, two_factor_enabled=True)
    _insert_test_user(db, username="TESTUSER01")

    app = _make_app(settings, db)

    from app.services.sso_service import SSOAuthError

    with patch(
        "app.routers.auth.SSOService.authenticate",
        new_callable=AsyncMock,
        side_effect=SSOAuthError("SSO credential rejected"),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "TESTUSER01", "password": "wrongpassword"},
            )

    assert resp.status_code == 401
    assert "access_token" not in resp.json()


# ---------------------------------------------------------------------------
# Test 2: TEST* user cannot skip 2FA (default allow_test_users=False)
# ---------------------------------------------------------------------------

def test_test_user_cannot_skip_2fa(db):
    """With allow_test_users=False (default), a TEST* username that passes
    SSO must still be required to complete 2FA (Step 1 returns code_id).

    The endpoint must return a LoginStep1Response (code_id), not a JWT.
    """
    settings = _make_settings(allow_test_users=False, two_factor_enabled=True)
    user = _insert_test_user(db, username="TESTUSER01")

    app = _make_app(settings, db)

    # Mock SSO to succeed
    sso_user_data = {
        "username": "TESTUSER01",
        "name": "Test User",
        "phone_number": "01012341234",
    }

    # Mock generate_code and _send_2fa_sms so no real SMS is sent
    with patch(
        "app.routers.auth.SSOService.authenticate",
        new_callable=AsyncMock,
        return_value=sso_user_data,
    ), patch(
        "app.routers.auth._fetch_oguard_profile",
        return_value=None,
    ), patch(
        "app.routers.auth.generate_code",
        return_value=("code-uuid-001", "123456"),
    ), patch(
        "app.routers.auth._send_2fa_sms",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "TESTUSER01", "password": "anypassword"},
            )

    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}: {resp.text}"
    body = resp.json()

    # Must return Step 1 (code_id), NOT a JWT
    assert "code_id" in body, (
        f"Expected 2FA step-1 response (code_id) but got: {body}"
    )
    assert "access_token" not in body, (
        f"TEST* user received JWT without completing 2FA: {body}"
    )


# ---------------------------------------------------------------------------
# Test 3: Bypass is functional when allow_test_users=True
# ---------------------------------------------------------------------------

def test_test_user_with_env_flag_sso_bypass(db):
    """With allow_test_users=True AND password 'test2026', a TEST* user
    must receive a JWT directly (SSO+2FA bypassed) — intended dev shortcut.
    """
    settings = _make_settings(allow_test_users=True, two_factor_enabled=True)
    _insert_test_user(db, username="TESTUSER01", is_approved=True)

    app = _make_app(settings, db)

    # SSO mock should NOT be called; if it is, we want it to blow up to
    # detect accidental SSO invocation.
    with patch(
        "app.routers.auth.SSOService.authenticate",
        new_callable=AsyncMock,
        side_effect=AssertionError("SSO should not be called during bypass"),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "TESTUSER01", "password": "test2026"},
            )

    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "access_token" in body, f"Expected JWT in response: {body}"
    assert body["username"] == "TESTUSER01"


def test_test_user_with_env_flag_2fa_bypass(db):
    """With allow_test_users=True, a TEST* user that passes SSO must not
    be required to complete 2FA — JWT should be issued directly after SSO.
    """
    settings = _make_settings(allow_test_users=True, two_factor_enabled=True)
    _insert_test_user(db, username="TESTUSER99", is_approved=True)

    app = _make_app(settings, db)

    sso_user_data = {
        "username": "TESTUSER99",
        "name": "Test User 99",
        "phone_number": "01099998888",
    }

    with patch(
        "app.routers.auth.SSOService.authenticate",
        new_callable=AsyncMock,
        return_value=sso_user_data,
    ), patch(
        "app.routers.auth._fetch_oguard_profile",
        return_value=None,
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            # Use a password that does NOT match "test2026" so the SSO+2FA
            # full bypass is not triggered — only the 2FA-skip path is tested.
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "TESTUSER99", "password": "normalpassword"},
            )

    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "access_token" in body, f"Expected JWT (2FA skipped) but got: {body}"
    assert "code_id" not in body


# ---------------------------------------------------------------------------
# Test 4: Unapproved TEST* user cannot bypass even with allow_test_users=True
# ---------------------------------------------------------------------------

def test_unapproved_test_user_cannot_bypass(db):
    """Even with allow_test_users=True, an unapproved TEST* user must not
    receive a JWT — the bypass only works for approved accounts.
    """
    settings = _make_settings(allow_test_users=True, two_factor_enabled=True)
    _insert_test_user(db, username="TESTUSER01", is_approved=False)

    app = _make_app(settings, db)

    with patch(
        "app.routers.auth.SSOService.authenticate",
        new_callable=AsyncMock,
        side_effect=AssertionError("SSO should not be called during bypass"),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "TESTUSER01", "password": "test2026"},
            )

    # Unapproved user: bypass check finds user but is_approved=False → no JWT issued.
    # The request falls through to SSO (mock raises AssertionError) → 500 error.
    # Any non-200 or a 200 without access_token is acceptable; just confirm no JWT.
    assert resp.status_code != 200 or "access_token" not in resp.json(), (
        f"Unapproved TEST* user should never receive a JWT: status={resp.status_code} body={resp.text}"
    )
