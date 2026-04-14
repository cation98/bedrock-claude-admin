"""Phase 1a: webui-verify silent refresh — ai-chat.skons.net 재로그인 박멸."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_settings
from app.core.jwt_rs256 import (
    create_access_token,
    create_refresh_token,
)

client = TestClient(app)


def test_webui_verify_no_cookies_returns_401():
    """쿠키 전혀 없음 → 401 Bearer."""
    resp = client.get("/api/v1/auth/webui-verify")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")


def test_webui_verify_expired_refresh_returns_401(create_test_user):
    """refresh 만료 → 401 (auth-signin으로 리다이렉트)."""
    user = create_test_user(username="N1102359")
    settings = get_settings()
    # 이미 만료된 refresh token 생성 (expires_delta=-1초)
    # create_refresh_token은 (token_str, jti) 튜플을 반환
    expired_refresh, _ = create_refresh_token(
        user.username, "", "", "user", settings,
        expires_delta=timedelta(seconds=-1),
    )
    resp = client.get(
        "/api/v1/auth/webui-verify",
        cookies={"bedrock_refresh": expired_refresh},
    )
    assert resp.status_code == 401
    # 만료된 refresh로는 재발급되지 않아야 함
    assert "bedrock_jwt=" not in resp.headers.get("Set-Cookie", "")
