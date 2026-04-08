"""X-Pod-Token 인증 시스템 단위 테스트.

get_current_user_or_pod dependency의 보안 검증:
- 유효한 Pod 토큰 → 인증 성공
- 잘못된 Pod 토큰 → 403
- X-Pod-Token 헤더 누락 → 403
- JWT 인증 정상 동작 (회귀)
- 해당 세션 없는 Pod 이름 → 403
"""

import hashlib
import secrets

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.config import Settings, get_settings
from app.core.security import (
    create_access_token,
    get_current_user_or_pod,
)
from app.models.user import User  # noqa: F401 — FK target table
from app.models.session import TerminalSession


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


def _test_settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        debug=False,
    )


# --------------- Minimal FastAPI test app ---------------

def _build_test_app() -> FastAPI:
    """get_current_user_or_pod를 사용하는 최소 FastAPI 앱."""
    app = FastAPI()

    @app.get("/test-pod-auth")
    async def _pod_auth_endpoint(
        current_user: dict = Depends(get_current_user_or_pod),
    ):
        return {"username": current_user["sub"], "auth_type": current_user.get("auth_type", "jwt")}

    return app


_app = _build_test_app()


@pytest.fixture()
def client(db):
    """TestClient with DB and settings overrides."""

    def _override_get_db():
        yield db

    _app.dependency_overrides[get_db] = _override_get_db
    _app.dependency_overrides[get_settings] = _test_settings

    with TestClient(_app, raise_server_exceptions=False) as tc:
        yield tc

    _app.dependency_overrides.clear()


# --------------- Helpers ---------------

def _make_pod_token() -> tuple[str, str]:
    """(raw_token, sha256_hash) 쌍을 생성."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token, token_hash


def _create_user(db, user_id: int = 1, username: str = "TESTUSER01"):
    user = User(id=user_id, username=username, name=username, role="user", is_approved=True)
    db.add(user)
    db.commit()


def _create_session(
    db,
    username: str = "TESTUSER01",
    pod_name: str = "claude-terminal-testuser01",
    pod_status: str = "running",
    pod_token_hash: str | None = None,
    user_id: int = 1,
):
    session = TerminalSession(
        user_id=user_id,
        username=username,
        pod_name=pod_name,
        pod_status=pod_status,
        pod_token_hash=pod_token_hash,
    )
    db.add(session)
    db.commit()
    return session


# --------------- Tests: Pod Token Auth ---------------


class TestPodTokenAuth:
    """X-Pod-Name + X-Pod-Token 헤더 조합 검증."""

    def test_valid_pod_token_succeeds(self, client, db):
        """유효한 Pod 토큰 → 200 + 사용자 정보 반환."""
        _create_user(db)
        token, token_hash = _make_pod_token()
        _create_session(db, pod_token_hash=token_hash)

        resp = client.get(
            "/test-pod-auth",
            headers={
                "X-Pod-Name": "claude-terminal-testuser01",
                "X-Pod-Token": token,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "TESTUSER01"
        assert data["auth_type"] == "pod"

    def test_invalid_pod_token_rejected(self, client, db):
        """잘못된 Pod 토큰 → 403."""
        _create_user(db)
        _, token_hash = _make_pod_token()
        _create_session(db, pod_token_hash=token_hash)

        resp = client.get(
            "/test-pod-auth",
            headers={
                "X-Pod-Name": "claude-terminal-testuser01",
                "X-Pod-Token": "totally-wrong-token",
            },
        )

        assert resp.status_code == 403
        assert "Invalid pod token" in resp.json()["detail"]

    def test_missing_pod_token_header_rejected(self, client, db):
        """X-Pod-Token 헤더 없이 X-Pod-Name만 있는 경우 → 403.

        핵심 보안 테스트: 기존 취약점(X-Pod-Name만으로 인증) 수정 확인.
        """
        _create_user(db)
        _, token_hash = _make_pod_token()
        _create_session(db, pod_token_hash=token_hash)

        resp = client.get(
            "/test-pod-auth",
            headers={"X-Pod-Name": "claude-terminal-testuser01"},
            # X-Pod-Token 없음
        )

        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert "X-Pod-Token" in detail

    def test_pod_name_without_matching_session_rejected(self, client, db):
        """DB에 세션이 없는 Pod 이름 → 403."""
        # DB에 아무 세션도 없음
        resp = client.get(
            "/test-pod-auth",
            headers={
                "X-Pod-Name": "claude-terminal-ghost",
                "X-Pod-Token": "any-token-value",
            },
        )

        assert resp.status_code == 403

    def test_terminated_session_rejected(self, client, db):
        """terminated 세션의 Pod → 403 (running/creating만 허용)."""
        _create_user(db)
        token, token_hash = _make_pod_token()
        _create_session(db, pod_status="terminated", pod_token_hash=token_hash)

        resp = client.get(
            "/test-pod-auth",
            headers={
                "X-Pod-Name": "claude-terminal-testuser01",
                "X-Pod-Token": token,
            },
        )

        assert resp.status_code == 403

    def test_session_without_pod_token_hash_rejected(self, client, db):
        """pod_token_hash가 없는 세션 (레거시 데이터) → 403."""
        _create_user(db)
        _create_session(db, pod_token_hash=None)  # 해시 없음

        resp = client.get(
            "/test-pod-auth",
            headers={
                "X-Pod-Name": "claude-terminal-testuser01",
                "X-Pod-Token": "any-token",
            },
        )

        assert resp.status_code == 403

    def test_creating_status_session_authenticated(self, client, db):
        """'creating' 상태의 세션도 인증 허용 (Pod 시작 직후 API 호출 지원)."""
        _create_user(db)
        token, token_hash = _make_pod_token()
        _create_session(db, pod_status="creating", pod_token_hash=token_hash)

        resp = client.get(
            "/test-pod-auth",
            headers={
                "X-Pod-Name": "claude-terminal-testuser01",
                "X-Pod-Token": token,
            },
        )

        assert resp.status_code == 200
        assert resp.json()["username"] == "TESTUSER01"


# --------------- Tests: JWT Auth Regression ---------------


class TestJwtAuthRegression:
    """JWT Bearer 토큰 인증이 계속 정상 작동하는지 확인."""

    def test_valid_jwt_token_succeeds(self, client):
        """유효한 JWT 토큰 → 200."""
        settings = _test_settings()
        token = create_access_token(
            {"sub": "JWTUSER01", "role": "user", "name": "JWT User"},
            settings=settings,
        )

        resp = client.get(
            "/test-pod-auth",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "JWTUSER01"
        assert data["auth_type"] == "jwt"

    def test_invalid_jwt_token_rejected(self, client):
        """잘못된 JWT 토큰 → 403 (JWT 실패 후 Pod 인증도 실패)."""
        resp = client.get(
            "/test-pod-auth",
            headers={"Authorization": "Bearer not-a-valid-jwt"},
        )

        assert resp.status_code == 403

    def test_jwt_takes_priority_over_pod_headers(self, client, db):
        """JWT 토큰이 있으면 Pod 헤더보다 우선 적용."""
        _create_user(db)
        token, token_hash = _make_pod_token()
        _create_session(db, pod_token_hash=token_hash)

        settings = _test_settings()
        jwt_token = create_access_token(
            {"sub": "JWTUSER01", "role": "admin"},
            settings=settings,
        )

        resp = client.get(
            "/test-pod-auth",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "X-Pod-Name": "claude-terminal-testuser01",
                "X-Pod-Token": token,
            },
        )

        assert resp.status_code == 200
        # JWT 인증이 우선: Pod 사용자(TESTUSER01)가 아닌 JWT 사용자(JWTUSER01)
        assert resp.json()["username"] == "JWTUSER01"

    def test_no_auth_rejected(self, client):
        """인증 정보 없음 → 403."""
        resp = client.get("/test-pod-auth")
        assert resp.status_code == 403


# --------------- Tests: Token Security Properties ---------------


class TestTokenSecurityProperties:
    """토큰 보안 속성 검증."""

    def test_different_token_hashes_for_different_tokens(self):
        """동일 토큰에서 생성된 해시는 동일, 다른 토큰은 다른 해시."""
        t1, h1 = _make_pod_token()
        t2, h2 = _make_pod_token()
        assert h1 != h2
        # 동일 토큰의 해시는 결정론적
        assert hashlib.sha256(t1.encode()).hexdigest() == h1

    def test_empty_token_rejected(self, client, db):
        """빈 X-Pod-Token 헤더 → 403."""
        _create_user(db)
        _, token_hash = _make_pod_token()
        _create_session(db, pod_token_hash=token_hash)

        resp = client.get(
            "/test-pod-auth",
            headers={
                "X-Pod-Name": "claude-terminal-testuser01",
                "X-Pod-Token": "",  # 빈 토큰
            },
        )

        assert resp.status_code == 403

    def test_non_pod_name_prefix_rejected(self, client, db):
        """claude-terminal- 접두사 없는 Pod 이름 → 403 (Pod 인증 경로 진입 안 함)."""
        _create_user(db)
        token, token_hash = _make_pod_token()
        _create_session(db, pod_name="my-pod-testuser01", pod_token_hash=token_hash)

        resp = client.get(
            "/test-pod-auth",
            headers={
                "X-Pod-Name": "my-pod-testuser01",  # 잘못된 접두사
                "X-Pod-Token": token,
            },
        )

        # Pod 인증 경로로 진입하지 않으므로 403
        assert resp.status_code == 403
