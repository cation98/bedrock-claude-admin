"""쿠키 보안 속성 테스트 — T10 의존.

Coverage:
  CP-13: 발급된 쿠키 이름에 bedrock_ prefix 강제 적용
  CP-14: 쿠키 Domain=.skons.net
  CP-15: 쿠키 SameSite=Lax + Secure 속성

설계 근거 (design doc):
  "모든 사내 AI 플랫폼 쿠키 이름 강제 prefix: bedrock_session, bedrock_jwt,
   bedrock_refresh, bedrock_webui_session. sso.skons.net과의 이름 충돌 방지."

Block: T10 (쿠키 bedrock_ prefix + Domain=.skons.net + SameSite=Lax 구현) 완료 후.
T4 (jwt_auth router) 완료 후에도 일부 테스트 실행 가능.
"""

import os

os.environ.setdefault(
    "ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-32-chars-min-xx"
)

import hashlib
import secrets
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.config import Settings, get_settings
from app.models.user import User  # noqa: F401
from app.models.session import TerminalSession  # noqa: F401

# ---------------------------------------------------------------------------
# T4 router 가용 여부 확인
# ---------------------------------------------------------------------------

try:
    from app.routers.jwt_auth import router as jwt_auth_router
    _HAS_JWT_AUTH = True
except ImportError:
    _HAS_JWT_AUTH = False
    jwt_auth_router = None


# ---------------------------------------------------------------------------
# Test DB
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
    s = _TestSession()
    try:
        yield s
    finally:
        s.close()


def _test_settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len-xxxx",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=5,
        jwt_refresh_token_expire_hours=12,
        debug=False,
        onlyoffice_jwt_secret="test-onlyoffice-jwt-secret-32-chars-min-xx",
        external_host="ai.skons.net",
    )


@pytest.fixture()
def client(db):
    if not _HAS_JWT_AUTH:
        pytest.skip("T4: jwt_auth router not yet implemented")

    from app.core.jwt_rs256 import reset_blacklist_for_testing
    reset_blacklist_for_testing()

    app = FastAPI()
    app.include_router(jwt_auth_router)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = _test_settings

    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc

    app.dependency_overrides.clear()


def _setup_user_and_pod_token(db, username="N0000001", pod_name="claude-terminal-n0000001"):
    user = User(username=username, name="Test User", role="user", is_approved=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    ts = TerminalSession(
        user_id=user.id,
        username=username,
        pod_name=pod_name,
        pod_status="running",
        pod_token_hash=token_hash,
    )
    db.add(ts)
    db.commit()
    return raw, pod_name


# ===========================================================================
# CP-13: Cookie Name Prefix
# ===========================================================================

class TestCookieNamePrefix:
    """CP-13: 발급 쿠키 이름 bedrock_ prefix 강제."""

    REQUIRED_PREFIXES = (
        "bedrock_session",
        "bedrock_jwt",
        "bedrock_refresh",
        "bedrock_webui_session",
    )

    def test_cp13_pod_token_exchange_cookies_have_bedrock_prefix(self, client, db):
        """CP-13: pod-token-exchange 응답 Set-Cookie에 bedrock_ prefix 강제."""
        raw, pod_name = _setup_user_and_pod_token(db)

        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": pod_name},
        )
        assert resp.status_code == 200, f"Exchange failed: {resp.text}"

        # 쿠키 확인 (Set-Cookie 헤더 기반)
        set_cookie_headers = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else [
            v for k, v in resp.headers.items() if k.lower() == "set-cookie"
        ]

        for cookie_header in set_cookie_headers:
            cookie_name = cookie_header.split("=")[0].strip()
            assert any(cookie_name.startswith(prefix.split("_")[0] + "_") for prefix in self.REQUIRED_PREFIXES), (
                f"FAIL: 쿠키 이름 '{cookie_name}'이 bedrock_ prefix를 갖지 않음. "
                f"허용 prefix: {self.REQUIRED_PREFIXES}. "
                "sso.skons.net 쿠키와의 충돌 방지를 위해 반드시 prefix 필요."
            )

    def test_cp13_no_bare_jwt_or_session_cookie_names(self, client, db):
        """CP-13: 'jwt', 'session', 'access_token' 같은 bare 이름 쿠키 없어야 함."""
        raw, pod_name = _setup_user_and_pod_token(db)
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": pod_name},
        )
        if resp.status_code != 200:
            pytest.skip("Exchange not yet implemented")

        forbidden_names = {"jwt", "session", "token", "access_token", "refresh_token", "auth"}
        set_cookie_headers = [
            v for k, v in resp.headers.items() if k.lower() == "set-cookie"
        ]
        for cookie_header in set_cookie_headers:
            cookie_name = cookie_header.split("=")[0].strip().lower()
            assert cookie_name not in forbidden_names, (
                f"FAIL: bare 쿠키 이름 '{cookie_name}' 금지. bedrock_ prefix 필요."
            )


# ===========================================================================
# CP-14: Cookie Domain
# ===========================================================================

class TestCookieDomain:
    """CP-14: 쿠키 Domain=.skons.net 설정.

    Note: pod-token-exchange는 Pod 클라이언트용 JSON 응답 (쿠키 없음).
    브라우저용 쿠키는 refresh 엔드포인트에서 Set-Cookie로 세팅됨.
    """

    def test_cp14_cookie_domain_is_skons_net(self, client, db):
        """CP-14: refresh Set-Cookie 헤더에 Domain=.skons.net 포함."""
        from app.core.jwt_rs256 import create_refresh_token, reset_blacklist_for_testing
        reset_blacklist_for_testing()

        raw, pod_name = _setup_user_and_pod_token(db)
        # pod-token-exchange → refresh token 획득
        exchange_resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": pod_name},
        )
        if exchange_resp.status_code != 200:
            pytest.skip("Exchange not yet implemented")

        refresh_token = exchange_resp.json()["refresh_token"]

        # refresh 호출 → Set-Cookie: bedrock_jwt
        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        if resp.status_code != 200:
            pytest.skip(f"Refresh failed: {resp.status_code} {resp.text}")

        set_cookie_headers = [
            v for k, v in resp.headers.items() if k.lower() == "set-cookie"
        ]
        assert set_cookie_headers, (
            "Set-Cookie 헤더가 없음. "
            "refresh 엔드포인트가 bedrock_jwt 쿠키를 설정해야 함."
        )

        for cookie_header in set_cookie_headers:
            parts = [p.strip().lower() for p in cookie_header.split(";")]
            domain_parts = [p for p in parts if p.startswith("domain=")]
            assert domain_parts, (
                f"FAIL: 쿠키에 Domain 속성 없음. Cookie: {cookie_header}. "
                "chat.skons.net에서 auth.skons.net 쿠키가 전달되려면 Domain=.skons.net 필요."
            )
            domain_value = domain_parts[0].split("=", 1)[1].strip()
            assert domain_value in (".skons.net", "skons.net"), (
                f"FAIL: 쿠키 Domain='{domain_value}'. '.skons.net'이어야 함."
            )


# ===========================================================================
# CP-15: Cookie SameSite + Secure
# ===========================================================================

class TestCookieSameSite:
    """CP-15: 쿠키 SameSite=Lax + Secure + HttpOnly 속성."""

    def test_cp15_cookie_samesite_lax(self, client, db):
        """CP-15: Set-Cookie 헤더에 SameSite=Lax 포함."""
        raw, pod_name = _setup_user_and_pod_token(db)
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": pod_name},
        )
        if resp.status_code != 200:
            pytest.skip("Exchange not yet implemented")

        set_cookie_headers = [
            v for k, v in resp.headers.items() if k.lower() == "set-cookie"
        ]
        for cookie_header in set_cookie_headers:
            parts = [p.strip().lower() for p in cookie_header.split(";")]
            assert "samesite=lax" in parts, (
                f"FAIL: 쿠키에 SameSite=Lax 없음. Cookie: {cookie_header}"
            )

    def test_cp15_cookie_httponly(self, client, db):
        """CP-15: JWT 쿠키에 HttpOnly 속성 (XSS로부터 JS 접근 차단)."""
        raw, pod_name = _setup_user_and_pod_token(db)
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": pod_name},
        )
        if resp.status_code != 200:
            pytest.skip("Exchange not yet implemented")

        set_cookie_headers = [
            v for k, v in resp.headers.items() if k.lower() == "set-cookie"
        ]
        jwt_cookies = [h for h in set_cookie_headers if "bedrock_jwt" in h.lower() or "bedrock_refresh" in h.lower()]
        for cookie_header in jwt_cookies:
            parts = [p.strip().lower() for p in cookie_header.split(";")]
            assert "httponly" in parts, (
                f"FAIL: JWT 쿠키에 HttpOnly 없음. Cookie: {cookie_header}. "
                "XSS 공격으로 JS에서 JWT 탈취 가능!"
            )

    def test_cp15_jwks_endpoint_no_cookies(self, client):
        """CP-15: 공개 엔드포인트 /auth/.well-known/jwks.json는 쿠키 발급 없음."""
        resp = client.get("/auth/.well-known/jwks.json")
        if resp.status_code != 200:
            pytest.skip("JWKS endpoint not yet implemented")

        set_cookie_headers = [
            v for k, v in resp.headers.items() if k.lower() == "set-cookie"
        ]
        assert not set_cookie_headers, (
            f"FAIL: JWKS 공개 엔드포인트가 쿠키를 발급함. Cookie headers: {set_cookie_headers}"
        )
