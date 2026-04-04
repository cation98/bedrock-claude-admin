"""프록시 인증 로직 단위 테스트.

proxy_server.py의 인증 파싱 및 세션 검증 로직을 검증.
네트워크 통신 없이 순수 로직만 테스트.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.session import TerminalSession
from app.models.user import User  # noqa: F401 -- FK target table registration
from app.proxy_server import _parse_proxy_auth, _validate_proxy_secret


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


def _create_user(db, user_id: int, username: str):
    """Create User record to satisfy FK constraint on TerminalSession.user_id."""
    user = User(id=user_id, username=username, name=username, role="user", is_approved=True)
    db.add(user)
    db.commit()


# --------------- Tests: Auth Parsing ---------------


def test_valid_user_secret_authenticated(db):
    """Valid USER_ID:secret combination is authenticated."""
    _create_user(db, 1, "TESTUSER01")
    db.add(TerminalSession(
        user_id=1,
        username="TESTUSER01",
        pod_name="claude-terminal-testuser01",
        pod_status="running",
        proxy_secret="abc123secret",
    ))
    db.commit()

    assert _validate_proxy_secret("TESTUSER01", "abc123secret", db) is True


def test_invalid_secret_rejected(db):
    """Invalid secret is rejected."""
    _create_user(db, 1, "TESTUSER01")
    db.add(TerminalSession(
        user_id=1,
        username="TESTUSER01",
        pod_name="claude-terminal-testuser01",
        pod_status="running",
        proxy_secret="correct_secret",
    ))
    db.commit()

    assert _validate_proxy_secret("TESTUSER01", "wrong_secret", db) is False


def test_missing_auth_header():
    """Missing Proxy-Authorization header fails parsing."""
    assert _parse_proxy_auth("") is None
    assert _parse_proxy_auth("Bearer token123") is None


def test_session_lookup_matches(db):
    """Session lookup: only 'running' sessions are valid."""
    _create_user(db, 1, "TESTUSER01")
    _create_user(db, 2, "TESTUSER02")

    # terminated session should be ignored
    db.add(TerminalSession(
        user_id=1,
        username="TESTUSER01",
        pod_name="claude-terminal-testuser01",
        pod_status="terminated",
        proxy_secret="old_secret",
    ))
    db.commit()

    assert _validate_proxy_secret("TESTUSER01", "old_secret", db) is False

    # running session should be accepted
    db.add(TerminalSession(
        user_id=2,
        username="TESTUSER02",
        pod_name="claude-terminal-testuser02",
        pod_status="running",
        proxy_secret="active_secret",
    ))
    db.commit()

    assert _validate_proxy_secret("TESTUSER02", "active_secret", db) is True


# --------------- Tests: Base64 Parsing ---------------


def test_parse_valid_basic_auth():
    """Valid Basic auth header is parsed correctly."""
    import base64
    encoded = base64.b64encode(b"USER01:mysecret123").decode()
    result = _parse_proxy_auth(f"Basic {encoded}")
    assert result == ("USER01", "mysecret123")


def test_parse_invalid_format():
    """Invalid format returns None."""
    assert _parse_proxy_auth("NotBasic abc123") is None
    # base64 but no colon
    import base64
    encoded = base64.b64encode(b"nocolon").decode()
    assert _parse_proxy_auth(f"Basic {encoded}") is None


# --------------- Tests: proxy_secret=None reuse path ---------------


def test_proxy_secret_none_reuse_preserves_existing(db):
    """When create_pod returns proxy_secret=None (Pod reuse), the existing
    session's proxy_secret should still authenticate successfully.

    Simulates the scenario where a user's Pod already exists and
    create_pod returns (pod_name, None). The caller should look up the
    previous session's proxy_secret from DB and reuse it.
    """
    _create_user(db, 1, "REUSEUSER01")

    # Simulate an existing running session with a known proxy_secret
    original_secret = "original_proxy_secret_abc123"
    db.add(TerminalSession(
        user_id=1,
        username="REUSEUSER01",
        pod_name="claude-terminal-reuseuser01",
        pod_status="running",
        proxy_secret=original_secret,
    ))
    db.commit()

    # Verify that the original secret still authenticates
    assert _validate_proxy_secret("REUSEUSER01", original_secret, db) is True

    # A None secret should NOT authenticate (guards against broken proxy auth)
    assert _validate_proxy_secret("REUSEUSER01", "", db) is False

    # Verify the existing session's proxy_secret can be looked up from DB
    existing = db.query(TerminalSession).filter(
        TerminalSession.pod_name == "claude-terminal-reuseuser01",
        TerminalSession.proxy_secret.isnot(None),
    ).order_by(TerminalSession.created_at.desc()).first()
    assert existing is not None
    assert existing.proxy_secret == original_secret
    # The looked-up secret should authenticate
    assert _validate_proxy_secret("REUSEUSER01", existing.proxy_secret, db) is True
