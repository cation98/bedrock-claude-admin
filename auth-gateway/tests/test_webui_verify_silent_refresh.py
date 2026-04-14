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


def test_webui_verify_expired_access_valid_refresh_silently_reissues(create_test_user):
    """access 만료 + refresh 유효 → 200 + Set-Cookie(bedrock_jwt)."""
    user = create_test_user(username="N1102359")
    settings = get_settings()
    # 만료된 access token 생성
    expired_access = create_access_token(
        user.username, "", "", "user", settings,
        expires_delta=timedelta(seconds=-1),
    )
    # 유효한 refresh token 생성 (12h 기본 TTL)
    # create_refresh_token은 (token_str, jti) 튜플을 반환
    valid_refresh, _ = create_refresh_token(
        user.username, "", "", "user", settings,
    )
    resp = client.get(
        "/api/v1/auth/webui-verify",
        cookies={
            "bedrock_jwt": expired_access,
            "bedrock_refresh": valid_refresh,
        },
    )
    assert resp.status_code == 200
    # 새 access cookie가 Set-Cookie로 내려와야 함
    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "bedrock_jwt=" in set_cookie, f"Expected new bedrock_jwt cookie, got: {set_cookie!r}"
    # X-SKO-Email 헤더 정상
    assert resp.headers.get("X-SKO-Email") == f"{user.username}@skons.net"
