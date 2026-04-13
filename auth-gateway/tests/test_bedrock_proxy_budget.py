"""Phase 1c CP-20 Budget Gate — /v1/messages 진입 시 quota 초과 차단.

케이스:
- quota 초과 + not unlimited → 429 token_quota_exceeded
- quota 미배정 (None) → 통과 (Bedrock 호출로 진행)
- quota unlimited → 통과
- quota 초과 직전 → 통과
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import get_current_user
from app.routers import bedrock_proxy


def _test_settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        debug=False,
        onlyoffice_jwt_secret="test-onlyoffice-jwt-secret-32-chars-min-xx",
    )


def _mock_user() -> dict:
    return {"sub": "TESTUSER01", "role": "user", "name": "Test User"}


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(bedrock_proxy.router)
    app.dependency_overrides[get_settings] = _test_settings
    app.dependency_overrides[get_current_user] = _mock_user
    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc
    app.dependency_overrides.clear()


def _body():
    return {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_quota_exceeded_returns_429(client):
    """is_exceeded=True + is_unlimited=False → 429."""
    quota = {
        "is_exceeded": True,
        "is_unlimited": False,
        "cost_limit_usd": 1.00,
        "current_usage_usd": 1.25,
        "refresh_cycle": "monthly",
        "cycle_start": "2026-04-01",
        "cycle_end": "2026-04-13",
    }
    with patch("app.routers.bedrock_proxy._check_user_quota", return_value=quota), \
         patch("app.routers.bedrock_proxy.SessionLocal", return_value=MagicMock()):
        resp = client.post("/v1/messages", json=_body())
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["error"] == "token_quota_exceeded"
    assert detail["cost_limit_usd"] == 1.00
    assert detail["current_usage_usd"] == 1.25


def test_quota_not_assigned_passes_gate(client):
    """quota 미배정(None) → 429 아님 (Bedrock 진입). boto3 mock으로 200 확인."""
    fake_response = MagicMock()
    fake_response.__getitem__.side_effect = lambda k: {
        "body": MagicMock(read=lambda: b'{"content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":1,"output_tokens":1}}')
    }[k]
    fake_bedrock = MagicMock()
    fake_bedrock.invoke_model.return_value = {
        "body": MagicMock(read=lambda: b'{"content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":1,"output_tokens":1}}')
    }
    with patch("app.routers.bedrock_proxy._check_user_quota", return_value=None), \
         patch("app.routers.bedrock_proxy.SessionLocal", return_value=MagicMock()), \
         patch("app.routers.bedrock_proxy.boto3.client", return_value=fake_bedrock):
        resp = client.post("/v1/messages", json=_body())
    # 429가 아님을 확인 (실제 경로는 200 또는 502 — 여기서는 gate 통과 확인이 핵심)
    assert resp.status_code != 429


def test_quota_unlimited_passes_gate(client):
    """is_exceeded=True + is_unlimited=True → 통과 (무제한 사용자)."""
    quota = {
        "is_exceeded": True,
        "is_unlimited": True,
        "cost_limit_usd": 0.0,
        "current_usage_usd": 9999.0,
        "refresh_cycle": "monthly",
        "cycle_start": "2026-04-01",
        "cycle_end": "2026-04-13",
    }
    fake_bedrock = MagicMock()
    fake_bedrock.invoke_model.return_value = {
        "body": MagicMock(read=lambda: b'{"content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":1,"output_tokens":1}}')
    }
    with patch("app.routers.bedrock_proxy._check_user_quota", return_value=quota), \
         patch("app.routers.bedrock_proxy.SessionLocal", return_value=MagicMock()), \
         patch("app.routers.bedrock_proxy.boto3.client", return_value=fake_bedrock):
        resp = client.post("/v1/messages", json=_body())
    assert resp.status_code != 429


def test_quota_under_limit_passes_gate(client):
    """is_exceeded=False → 통과."""
    quota = {
        "is_exceeded": False,
        "is_unlimited": False,
        "cost_limit_usd": 10.00,
        "current_usage_usd": 5.00,
        "refresh_cycle": "monthly",
        "cycle_start": "2026-04-01",
        "cycle_end": "2026-04-13",
    }
    fake_bedrock = MagicMock()
    fake_bedrock.invoke_model.return_value = {
        "body": MagicMock(read=lambda: b'{"content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":1,"output_tokens":1}}')
    }
    with patch("app.routers.bedrock_proxy._check_user_quota", return_value=quota), \
         patch("app.routers.bedrock_proxy.SessionLocal", return_value=MagicMock()), \
         patch("app.routers.bedrock_proxy.boto3.client", return_value=fake_bedrock):
        resp = client.post("/v1/messages", json=_body())
    assert resp.status_code != 429
