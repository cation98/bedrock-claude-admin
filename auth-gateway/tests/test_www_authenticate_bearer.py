"""Phase 1a: WWW-Authenticate 헤더 Bearer 표준 (RFC 6750) 회귀 방지."""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_auth_me_unauth_returns_bearer_header():
    """미인증 상태 /api/v1/auth/me 호출 → WWW-Authenticate: Bearer."""
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    auth_header = resp.headers.get("WWW-Authenticate", "")
    assert auth_header.startswith("Bearer"), f"Expected Bearer, got: {auth_header!r}"
    assert 'realm="skons.net"' in auth_header, f"Expected realm, got: {auth_header!r}"


def test_webui_verify_unauth_returns_bearer_header():
    """webui-verify 401 응답도 Bearer."""
    resp = client.get("/api/v1/auth/webui-verify")
    assert resp.status_code == 401
    auth_header = resp.headers.get("WWW-Authenticate", "")
    assert auth_header.startswith("Bearer"), f"Expected Bearer, got: {auth_header!r}"
    assert 'realm="skons.net"' in auth_header, f"Expected realm, got: {auth_header!r}"
