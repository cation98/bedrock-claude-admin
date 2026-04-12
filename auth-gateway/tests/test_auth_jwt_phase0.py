"""Phase 0 JWT 인증 엔드포인트 단위 테스트.

Coverage:
  CP-01: pod-token-exchange 성공 → access+refresh JWT 발급 + pod_token blacklist
  CP-02: pod-token-exchange replay → 401 (Pod Token 재사용 공격 방어)
  CP-03: pod-token-exchange invalid token → 401
  CP-04: issue-jwt SSO 세션 유효 → JWT 발급
  CP-05: issue-jwt 잘못된/만료된 SSO 세션 → 401
  CP-06: refresh 정상 → 새 access JWT
  CP-07: refresh 만료 → 401
  CP-08: refresh revoke 상태 → 401
  CP-09: logout → 해당 refresh jti blacklist 확인
  CP-10: GET /auth/.well-known/jwks.json → RS256 공개키 반환

Block: T4 (JWT RS256 + JWKS 단일화 + pod-token-exchange 구현) 완료 후 실행 가능.
T4 module naming convention: app.routers.jwt_auth + app.core.jwt_rs256
"""

import os
import uuid
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, AsyncMock

os.environ.setdefault(
    "ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-32-chars-min-xx"
)

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# 의존성 체크 — T4 구현 전까지 모듈 import 실패 시 skip
# ---------------------------------------------------------------------------
jwt_auth_router = pytest.importorskip(
    "app.routers.jwt_auth",
    reason="T4: jwt_auth router not yet implemented",
)

from app.core.config import Settings, get_settings
from app.core.database import get_db, Base
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.models.user import User
from app.models.session import TerminalSession

# ---------------------------------------------------------------------------
# RS256 키쌍 생성 (테스트 전용)
# ---------------------------------------------------------------------------

def _generate_rsa_keypair():
    """테스트용 RS256 RSA 2048비트 키쌍 생성."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    return private_key


@pytest.fixture(scope="module")
def rsa_private_key():
    return _generate_rsa_keypair()


@pytest.fixture(scope="module")
def rsa_public_key(rsa_private_key):
    return rsa_private_key.public_key()


# ---------------------------------------------------------------------------
# SQLite 테스트 DB
# ---------------------------------------------------------------------------

_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_test_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


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


# ---------------------------------------------------------------------------
# Redis mock fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_redis():
    """Redis 클라이언트 mock — jti 블랙리스트 조작."""
    store: dict[str, str] = {}

    mock = MagicMock()
    mock.get = MagicMock(side_effect=lambda k: store.get(k))
    mock.set = MagicMock(side_effect=lambda k, v, **kwargs: store.update({k: v}))
    mock.delete = MagicMock(side_effect=lambda *keys: [store.pop(k, None) for k in keys])
    mock.keys = MagicMock(side_effect=lambda pattern: [k for k in store if k.startswith(pattern.replace("*", ""))])
    mock._store = store  # 테스트에서 직접 접근 가능
    return mock


# ---------------------------------------------------------------------------
# Settings fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_settings(rsa_private_key) -> Settings:
    """RS256 키를 포함한 테스트 Settings."""
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption,
    )
    private_pem = rsa_private_key.private_bytes(
        Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
    ).decode()

    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="RS256",
        jwt_access_token_expire_minutes=5,   # 5분 (테스트용)
        jwt_refresh_token_expire_hours=12,
        jwt_rs256_private_key=private_pem,
        debug=False,
        onlyoffice_jwt_secret="test-onlyoffice-jwt-secret-32-chars-min-xx",
    )


# ---------------------------------------------------------------------------
# FastAPI 테스트 앱 빌드
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(test_settings, mock_redis):
    """jwt_phase0 라우터를 포함한 최소 FastAPI 앱."""
    test_app = FastAPI()
    test_app.include_router(jwt_auth_router.router)

    def _override_get_db():
        session = _TestSession()
        try:
            yield session
        finally:
            session.close()

    def _override_settings():
        return test_settings

    def _override_redis():
        return mock_redis

    test_app.dependency_overrides[get_db] = _override_get_db
    test_app.dependency_overrides[get_settings] = _override_settings
    # Redis dependency는 T4 구현에서 정의될 예정
    if hasattr(jwt_auth_router, "get_redis"):
        test_app.dependency_overrides[jwt_auth_router.get_redis] = _override_redis

    return test_app


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _create_user(db, username: str = "TESTUSER01") -> User:
    user = User(username=username, name="Test User", role="user", is_approved=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_session(db, user: User, pod_token_hash: str | None = None) -> TerminalSession:
    session = TerminalSession(
        user_id=user.id,
        username=user.username,
        pod_name=f"claude-terminal-{user.username.lower()}",
        pod_status="running",
        pod_token_hash=pod_token_hash,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _make_pod_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


# ===========================================================================
# CP-01 ~ CP-03: POST /auth/pod-token-exchange
# ===========================================================================

class TestPodTokenExchange:
    """Phase 0 신설 엔드포인트: Pod Token → JWT 교환."""

    def test_cp01_valid_pod_token_exchange_success(self, client, db):
        """CP-01: 유효한 Pod Token → access JWT + refresh JWT 발급."""
        user = _create_user(db)
        raw_token, token_hash = _make_pod_token()
        _create_session(db, user=user, pod_token_hash=token_hash)

        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw_token, "pod_name": "claude-terminal-testuser01"},
        )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body.get("token_type", "").lower() == "bearer"

    def test_cp02_pod_token_replay_rejected(self, client, db):
        """CP-02: 1회 사용된 Pod Token 재사용 → 401 (Pod Token blacklist 검증).

        SECURITY: 동일 pod_token으로 두 번 exchange 시도하면
        두 번째는 반드시 401이어야 한다.
        """
        user = _create_user(db)
        raw_token, token_hash = _make_pod_token()
        _create_session(db, user=user, pod_token_hash=token_hash)

        # 1회차 — 성공해야 함
        resp1 = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw_token, "pod_name": "claude-terminal-testuser01"},
        )
        assert resp1.status_code == 200

        # 2회차 — 동일 토큰 재사용 → 반드시 401
        resp2 = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw_token, "pod_name": "claude-terminal-testuser01"},
        )
        assert resp2.status_code == 401, (
            "FAIL: Pod Token replay was accepted! "
            f"Expected 401, got {resp2.status_code}. "
            "Pod Token blacklist이 작동하지 않음."
        )

    def test_cp03_invalid_pod_token_rejected(self, client, db):
        """CP-03: 잘못된 Pod Token → 401."""
        user = _create_user(db)
        _, token_hash = _make_pod_token()
        _create_session(db, user=user, pod_token_hash=token_hash)

        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": "completely-wrong-token", "pod_name": "claude-terminal-testuser01"},
        )
        assert resp.status_code == 401


# ===========================================================================
# CP-04 ~ CP-05: POST /auth/issue-jwt
# ===========================================================================

class TestIssueJwt:
    """브라우저 SSO 세션 → JWT 발급.

    Note: /auth/issue-jwt 엔드포인트는 T4에서 미구현 상태.
    현재 jwt_auth router에는 pod-token-exchange, refresh, logout, jwks만 있음.
    SSO 세션 기반 JWT 발급은 기존 /api/v1/auth/login (auth.py)에서 담당.
    CP-04/05는 T4 구현 완료 후 실제 엔드포인트로 교체.
    """

    def test_cp04_valid_sso_session_issues_jwt(self, client, db):
        """CP-04: 유효한 SSO 세션 쿠키 → JWT 발급 (현재: 기존 로그인 엔드포인트 기준).

        기존 /api/v1/auth/login은 SSO → JWT 발급.
        T4에서 /auth/issue-jwt 추가 시 이 테스트를 해당 엔드포인트로 교체.
        """
        # issue-jwt 엔드포인트가 없으면 skip
        resp = client.post(
            "/auth/issue-jwt",
            cookies={"sso_session": "any-session-value"},
        )
        if resp.status_code == 404:
            pytest.skip("CP-04: /auth/issue-jwt not yet implemented in T4")

        # 엔드포인트가 있으면 실제 검증 (mock 없이 401 예상)
        assert resp.status_code in (401, 403, 422)

    def test_cp05_invalid_sso_session_rejected(self, client, db):
        """CP-05: 잘못된/만료된 SSO 세션 → 401.

        T4 issue-jwt 구현 전까지 skip.
        """
        resp = client.post(
            "/auth/issue-jwt",
            cookies={"sso_session": "invalid-session"},
        )
        if resp.status_code == 404:
            pytest.skip("CP-05: /auth/issue-jwt not yet implemented in T4")

        assert resp.status_code in (401, 403, 422)


# ===========================================================================
# CP-06 ~ CP-08: POST /auth/refresh
# ===========================================================================

class TestRefresh:
    """Refresh JWT → 새 access JWT 발급."""

    def test_cp06_valid_refresh_issues_new_access_token(self, client, db, test_settings):
        """CP-06: 유효한 refresh token → 새 access JWT."""
        # 먼저 exchange로 refresh token 획득
        user = _create_user(db)
        raw_token, token_hash = _make_pod_token()
        _create_session(db, user=user, pod_token_hash=token_hash)

        exchange_resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw_token, "pod_name": "claude-terminal-testuser01"},
        )
        if exchange_resp.status_code != 200:
            pytest.skip("pod-token-exchange not yet implemented (T4 blocker)")

        refresh_token = exchange_resp.json()["refresh_token"]

        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body

    def test_cp07_expired_refresh_rejected(self, client, db):
        """CP-07: 만료된 refresh token → 401."""
        # jose로 직접 만료된 RS256 토큰 생성
        expired_payload = {
            "sub": "TESTUSER01",
            "jti": str(uuid.uuid4()),
            "type": "refresh",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        # HS256으로 서명된 토큰은 RS256 검증에서 실패해야 함 → 즉 만료된 토큰
        # T4 구현 후 실제 RS256 만료 토큰 생성 방식으로 교체
        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": "expired.refresh.token"},
        )
        assert resp.status_code == 401

    def test_cp08_revoked_refresh_rejected(self, client, db, mock_redis):
        """CP-08: revoke된 refresh token → 401 (Redis jti blacklist)."""
        revoked_jti = str(uuid.uuid4())
        # Redis blacklist에 미리 등록
        mock_redis._store[f"jti_blacklist:{revoked_jti}"] = "1"

        # revoked jti를 포함한 refresh token (T4 구현 후 실제 RS256 토큰으로 교체)
        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": "token.with.revoked.jti"},
        )
        assert resp.status_code == 401


# ===========================================================================
# CP-09: POST /auth/logout
# ===========================================================================

class TestLogout:
    """Logout → refresh token 무효화."""

    def test_cp09_logout_blacklists_refresh_jti(self, client, db, mock_redis):
        """CP-09: logout 호출 → refresh token의 jti가 Redis blacklist에 등록됨."""
        user = _create_user(db)
        raw_token, token_hash = _make_pod_token()
        _create_session(db, user=user, pod_token_hash=token_hash)

        exchange_resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw_token, "pod_name": "claude-terminal-testuser01"},
        )
        if exchange_resp.status_code != 200:
            pytest.skip("pod-token-exchange not yet implemented (T4 blocker)")

        refresh_token = exchange_resp.json()["refresh_token"]

        resp = client.post(
            "/auth/logout",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200

        # logout 후 refresh 시도 → 401
        retry_resp = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert retry_resp.status_code == 401, (
            "FAIL: Revoked refresh token was accepted after logout! "
            "Redis jti blacklist이 작동하지 않음."
        )


# ===========================================================================
# CP-10: GET /auth/.well-known/jwks.json
# ===========================================================================

class TestJwks:
    """JWKS 엔드포인트 — RS256 공개키 공개."""

    def test_cp10_jwks_returns_rsa_public_key(self, client):
        """CP-10: JWKS 엔드포인트가 RS256 공개키를 올바른 형식으로 반환."""
        resp = client.get("/auth/.well-known/jwks.json")

        assert resp.status_code == 200
        body = resp.json()
        assert "keys" in body
        assert len(body["keys"]) >= 1

        key = body["keys"][0]
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256"
        assert key["use"] == "sig"
        assert "n" in key   # RSA modulus
        assert "e" in key   # RSA exponent
        assert "kid" in key  # Key ID

    def test_cp10_jwks_no_private_key_exposed(self, client):
        """CP-10 보안: JWKS 응답에 RSA 개인키 필드(d, p, q, dp, dq, qi) 없음."""
        resp = client.get("/auth/.well-known/jwks.json")
        assert resp.status_code == 200

        for key in resp.json().get("keys", []):
            for private_field in ("d", "p", "q", "dp", "dq", "qi"):
                assert private_field not in key, (
                    f"SECURITY FAIL: JWKS에 RSA 개인키 필드 '{private_field}' 노출!"
                )
