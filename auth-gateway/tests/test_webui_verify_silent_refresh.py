"""Phase 1a: webui-verify silent refresh — ai-chat.skons.net 재로그인 박멸."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_webui_verify_no_cookies_returns_401():
    """쿠키 전혀 없음 → 401 Bearer."""
    resp = client.get("/api/v1/auth/webui-verify")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")
