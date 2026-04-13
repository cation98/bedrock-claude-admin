"""JWT TTL 확장 — issue #27 단위 테스트.

Coverage:
  - create_access_token: expires_delta, extra_claims 파라미터 정상 반영
  - create_refresh_token: 동일 (Task 2에서 추가)
  - 기본값 (None) 시 기존 동작 유지 (회귀 방지)
"""

import os
from datetime import timedelta

os.environ.setdefault(
    "ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-32-chars-min-xx"
)

import pytest
import jwt as pyjwt

from app.core.jwt_rs256 import (
    create_access_token,
    create_refresh_token,
)
from app.core.config import get_settings


@pytest.fixture
def rsa_settings(monkeypatch):
    """RS256 ephemeral key가 생성되도록 환경 구성."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    monkeypatch.setenv("JWT_RS256_PRIVATE_KEY", pem)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_create_access_token_default_ttl_uses_config(rsa_settings):
    """expires_delta 미지정 시 settings.jwt_rs256_access_expire_minutes 적용."""
    token = create_access_token("N1102359", "N1102359", "a@b.com", "user", rsa_settings)
    payload = pyjwt.decode(token, options={"verify_signature": False})
    expected_ttl = rsa_settings.jwt_rs256_access_expire_minutes * 60
    actual_ttl = payload["exp"] - payload["iat"]
    assert abs(actual_ttl - expected_ttl) < 5  # 5초 오차 허용


def test_create_access_token_expires_delta_override(rsa_settings):
    """expires_delta 지정 시 해당 값 적용."""
    token = create_access_token(
        "N1102359", "N1102359", "a@b.com", "user", rsa_settings,
        expires_delta=timedelta(hours=8),
    )
    payload = pyjwt.decode(token, options={"verify_signature": False})
    actual_ttl = payload["exp"] - payload["iat"]
    assert abs(actual_ttl - 8 * 3600) < 5


def test_create_access_token_extra_claims_embedded(rsa_settings):
    """extra_claims 지정 시 페이로드에 포함."""
    token = create_access_token(
        "N1102359", "N1102359", "a@b.com", "user", rsa_settings,
        extra_claims={"session_type": "pod"},
    )
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert payload.get("session_type") == "pod"


def test_create_access_token_no_extra_claims_absent(rsa_settings):
    """extra_claims 미지정 시 session_type 키 자체가 부재."""
    token = create_access_token("N1102359", "N1102359", "a@b.com", "user", rsa_settings)
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert "session_type" not in payload


def test_create_refresh_token_expires_delta_override(rsa_settings):
    """create_refresh_token도 expires_delta 적용."""
    token, _jti = create_refresh_token(
        "N1102359", "N1102359", "a@b.com", "user", rsa_settings,
        expires_delta=timedelta(hours=24),
    )
    payload = pyjwt.decode(token, options={"verify_signature": False})
    actual_ttl = payload["exp"] - payload["iat"]
    assert abs(actual_ttl - 24 * 3600) < 5


def test_create_refresh_token_extra_claims_embedded(rsa_settings):
    """refresh token도 extra_claims 반영."""
    token, _jti = create_refresh_token(
        "N1102359", "N1102359", "a@b.com", "user", rsa_settings,
        extra_claims={"session_type": "pod"},
    )
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert payload.get("session_type") == "pod"


# ─── /auth/refresh session_type 상속 (함수 레벨) ───────────────────────────

def test_refresh_route_with_pod_session_type_uses_8h(rsa_settings):
    """함수 레벨 — /auth/refresh가 session_type='pod' 페이로드를 받으면
    create_access_token을 expires_delta=8h + extra_claims={'session_type': 'pod'}로 호출."""
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routers import jwt_auth as mod

    captured = {}

    def fake_create_access_token(sub, emp_no, email, role, settings, expires_delta=None, extra_claims=None):
        captured["expires_delta"] = expires_delta
        captured["extra_claims"] = extra_claims
        return "fake-new-access"

    fake_payload = {
        "sub": "N1102359", "emp_no": "N1102359", "email": "a@b.com",
        "role": "user", "jti": "test-jti-pod", "session_type": "pod",
    }

    with patch.object(mod, "_verify_jwt_signature_only", return_value=fake_payload), \
         patch.object(mod, "is_user_revoked", return_value=False), \
         patch.object(mod, "is_jti_blacklisted", return_value=False), \
         patch.object(mod, "blacklist_jti"), \
         patch.object(mod, "write_access_cookies"), \
         patch.object(mod, "create_access_token", side_effect=fake_create_access_token):
        client = TestClient(app)
        resp = client.post("/auth/refresh", json={"refresh_token": "stub-refresh"})
        assert resp.status_code == 200, resp.text

    assert captured["expires_delta"] == timedelta(hours=8)
    assert captured["extra_claims"] == {"session_type": "pod"}


def test_refresh_route_without_session_type_uses_default(rsa_settings):
    """함수 레벨 — session_type 없는 refresh payload (SSO 경로)은 기본 TTL
    (expires_delta=None, extra_claims=None)로 create_access_token 호출."""
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routers import jwt_auth as mod

    captured = {}

    def fake_create_access_token(sub, emp_no, email, role, settings, expires_delta=None, extra_claims=None):
        captured["expires_delta"] = expires_delta
        captured["extra_claims"] = extra_claims
        return "fake-new-access"

    fake_payload = {
        "sub": "N1102359", "emp_no": "N1102359", "email": "a@b.com",
        "role": "user", "jti": "test-jti-sso",
    }

    with patch.object(mod, "_verify_jwt_signature_only", return_value=fake_payload), \
         patch.object(mod, "is_user_revoked", return_value=False), \
         patch.object(mod, "is_jti_blacklisted", return_value=False), \
         patch.object(mod, "blacklist_jti"), \
         patch.object(mod, "write_access_cookies"), \
         patch.object(mod, "create_access_token", side_effect=fake_create_access_token):
        client = TestClient(app)
        resp = client.post("/auth/refresh", json={"refresh_token": "stub-refresh"})
        assert resp.status_code == 200, resp.text

    assert captured["expires_delta"] is None
    assert captured["extra_claims"] is None
