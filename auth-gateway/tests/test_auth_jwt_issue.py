"""T4 TDD — JWT RS256 JWKS + pod-token-exchange + refresh + logout 테스트.

테스트 플랜 기준:
  - JWKS endpoint: RSA 공개키, Cache-Control
  - pod-token-exchange: 유효 토큰 교환, RS256 서명, 1회 사용 후 blacklist
  - refresh: 유효 refresh → 새 access, 무효 토큰 거부
  - logout: refresh jti blacklist, 이후 refresh 거부

RED → GREEN → REFACTOR 순서 준수.
"""

import hashlib
import os

os.environ.setdefault(
    "ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-32-chars-min-xx"
)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.config import Settings, get_settings

# models — Base.metadata 등록용
from app.models.user import User  # noqa: F401
from app.models.session import TerminalSession  # noqa: F401


# ─── SQLite 인메모리 테스트 DB ────────────────────────────────────────────────

_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(bind=_test_engine)


def _test_settings() -> Settings:
    """결정적 설정값 반환 (JWT 만료시간 15분 / 12시간)."""
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len-xxxx",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=15,
        jwt_refresh_token_expire_hours=12,
        debug=False,
        onlyoffice_jwt_secret="test-onlyoffice-jwt-secret-32-chars-min-xx",
    )


def _build_test_app() -> FastAPI:
    """jwt_auth 라우터만 포함한 경량 FastAPI 앱."""
    from app.routers.jwt_auth import router as jwt_auth_router

    app = FastAPI(title="T4 JWT Test App")
    app.include_router(jwt_auth_router)
    return app


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _setup_tables():
    """테스트마다 테이블 생성 후 삭제."""
    Base.metadata.create_all(bind=_test_engine)
    yield
    Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture(autouse=True)
def _clear_blacklist():
    """테스트마다 인메모리 블랙리스트 초기화."""
    from app.core.jwt_rs256 import reset_blacklist_for_testing
    reset_blacklist_for_testing()
    yield
    reset_blacklist_for_testing()


@pytest.fixture()
def db_session():
    session = _TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    """테스트 전용 TestClient (DB + Settings override)."""
    app = _build_test_app()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = _test_settings

    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc

    app.dependency_overrides.clear()


@pytest.fixture()
def approved_user(db_session) -> User:
    """승인된 테스트 사용자 생성."""
    user = User(
        username="N1102359",
        name="Test User",
        role="user",
        is_approved=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def active_session(db_session, approved_user):
    """활성 터미널 세션 + 원본 pod_token 반환."""
    raw_token = "test-pod-secret-token-abc-12345"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    ts = TerminalSession(
        user_id=approved_user.id,
        username=approved_user.username,
        pod_name="claude-terminal-n1102359",
        pod_status="running",
        pod_token_hash=token_hash,
    )
    db_session.add(ts)
    db_session.commit()
    db_session.refresh(ts)
    return ts, raw_token


# ─── JWKS endpoint ───────────────────────────────────────────────────────────

class TestJwksEndpoint:
    def test_jwks_returns_200(self, client):
        """JWKS endpoint는 200을 반환해야 한다."""
        resp = client.get("/auth/.well-known/jwks.json")
        assert resp.status_code == 200

    def test_jwks_has_rsa_key_structure(self, client):
        """JWKS 응답에 RSA 공개키(kty=RSA, use=sig, alg=RS256, n, e, kid)가 포함된다."""
        resp = client.get("/auth/.well-known/jwks.json")
        data = resp.json()
        assert "keys" in data
        assert len(data["keys"]) == 1
        key = data["keys"][0]
        assert key["kty"] == "RSA"
        assert key["use"] == "sig"
        assert key["alg"] == "RS256"
        assert "n" in key   # modulus (Base64URL)
        assert "e" in key   # exponent (Base64URL)
        assert "kid" in key

    def test_jwks_has_cache_control_header(self, client):
        """JWKS 응답에는 Cache-Control max-age=3600 헤더가 있어야 한다."""
        resp = client.get("/auth/.well-known/jwks.json")
        cc = resp.headers.get("cache-control", "")
        assert "3600" in cc


# ─── Pod Token Exchange ───────────────────────────────────────────────────────

class TestPodTokenExchange:
    def test_valid_exchange_returns_both_tokens(self, client, active_session):
        """유효한 Pod Token으로 access_token + refresh_token을 교환한다."""
        ts, raw = active_session
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": ts.pod_name},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 15 * 60

    def test_access_token_is_signed_rs256(self, client, active_session):
        """발급된 access_token JWT header alg는 RS256이어야 한다."""
        import base64, json as jlib
        ts, raw = active_session
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": ts.pod_name},
        )
        token = resp.json()["access_token"]
        header_b64 = token.split(".")[0]
        # Base64URL 패딩 보정
        pad = (4 - len(header_b64) % 4) % 4
        header = jlib.loads(base64.urlsafe_b64decode(header_b64 + "=" * pad))
        assert header["alg"] == "RS256"

    def test_access_token_sub_is_user_id(self, client, active_session, approved_user):
        """access_token sub claim은 users.id(str)이어야 한다."""
        from app.core.jwt_rs256 import verify_jwt
        ts, raw = active_session
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": ts.pod_name},
        )
        token = resp.json()["access_token"]
        payload = verify_jwt(token, expected_type="access")
        assert payload["sub"] == str(approved_user.id)

    def test_pod_token_replay_rejected_with_401(self, client, active_session):
        """동일 Pod Token을 두 번 교환하면 두 번째는 401을 반환한다."""
        ts, raw = active_session
        payload = {"pod_token": raw, "pod_name": ts.pod_name}

        resp1 = client.post("/auth/pod-token-exchange", json=payload)
        assert resp1.status_code == 200

        resp2 = client.post("/auth/pod-token-exchange", json=payload)
        assert resp2.status_code == 401

    def test_wrong_pod_name_returns_401(self, client, active_session):
        """존재하지 않는 pod_name은 401을 반환한다."""
        _, raw = active_session
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": "nonexistent-pod"},
        )
        assert resp.status_code == 401

    def test_wrong_pod_token_returns_401(self, client, active_session):
        """잘못된 pod_token은 401을 반환한다."""
        ts, _ = active_session
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": "wrong-token-value", "pod_name": ts.pod_name},
        )
        assert resp.status_code == 401


# ─── Refresh ─────────────────────────────────────────────────────────────────

class TestRefresh:
    def _get_refresh_token(self, client, active_session) -> str:
        ts, raw = active_session
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": ts.pod_name},
        )
        assert resp.status_code == 200
        return resp.json()["refresh_token"]

    def test_valid_refresh_returns_new_access(self, client, active_session):
        """유효한 refresh_token으로 새 access_token을 발급받는다."""
        refresh_token = self._get_refresh_token(client, active_session)
        resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_invalid_refresh_token_returns_401(self, client):
        """유효하지 않은 refresh_token은 401을 반환한다."""
        resp = client.post(
            "/auth/refresh", json={"refresh_token": "bad.token.here"}
        )
        assert resp.status_code == 401

    def test_no_refresh_token_returns_401(self, client):
        """refresh_token 없이 호출하면 401을 반환한다."""
        resp = client.post("/auth/refresh", json={})
        assert resp.status_code == 401

    def test_access_token_as_refresh_returns_401(self, client, active_session):
        """access_token을 refresh endpoint에 사용하면 401을 반환한다."""
        ts, raw = active_session
        resp0 = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": ts.pod_name},
        )
        access_token = resp0.json()["access_token"]
        resp = client.post(
            "/auth/refresh", json={"refresh_token": access_token}
        )
        assert resp.status_code == 401


# ─── Logout ──────────────────────────────────────────────────────────────────

class TestLogout:
    def _exchange(self, client, active_session) -> dict:
        ts, raw = active_session
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": ts.pod_name},
        )
        assert resp.status_code == 200
        return resp.json()

    def test_logout_returns_200(self, client, active_session):
        """로그아웃은 200을 반환한다."""
        tokens = self._exchange(client, active_session)
        resp = client.post(
            "/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        )
        assert resp.status_code == 200

    def test_refresh_after_logout_returns_401(self, client, active_session):
        """로그아웃 후 refresh_token 재사용은 401을 반환한다."""
        tokens = self._exchange(client, active_session)
        refresh_token = tokens["refresh_token"]

        client.post("/auth/logout", json={"refresh_token": refresh_token})

        resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 401

    def test_logout_without_token_returns_200(self, client):
        """refresh_token 없이 로그아웃해도 200을 반환한다."""
        resp = client.post("/auth/logout", json={})
        assert resp.status_code == 200
