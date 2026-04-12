"""`/api/v1/ai/chat/completions` endpoint 통합 테스트.

커버 범위 (docs/plans/2026-04-12-onlyoffice-ai-integration-test-plan.md):
  - 인증 실패 (Bearer 없음) → 401
  - 잘못된 body (messages 누락) → 400 OpenAI error schema
  - 비스트리밍 happy path → 200 OpenAI chat.completion schema
  - 스트리밍 happy path → SSE chunks
  - ★ client disconnect mid-stream → Bedrock stream.close() 호출 (critical regression)
  - Bedrock ClientError → 매핑된 HTTP status + OpenAI error body
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import get_current_user
from app.routers import ai as ai_router_mod


_DEFAULT_USER = {"sub": "TESTUSER01", "role": "user", "name": "Test User"}


def _settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        onlyoffice_jwt_secret="test-onlyoffice-jwt-secret-32-chars-min-xx",
        bedrock_region="ap-northeast-2",
        debug=False,
    )


def _build_app() -> FastAPI:
    app = FastAPI(title="test-ai")
    app.include_router(ai_router_mod.router)
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_current_user] = lambda: _DEFAULT_USER.copy()
    return app


@pytest.fixture
def client():
    return TestClient(_build_app(), raise_server_exceptions=False)


# ─── 기본 스키마 ──────────────────────────────────────────────────────────────

class TestSchemaValidation:
    def test_empty_body_returns_400(self, client):
        r = client.post("/api/v1/ai/chat/completions", json={})
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["type"] == "invalid_request_error"

    def test_missing_messages_returns_400(self, client):
        r = client.post("/api/v1/ai/chat/completions", json={"model": "claude-sonnet-4-6"})
        assert r.status_code == 400
        assert "must not be empty" in r.json()["detail"]["error"]["message"]

    def test_assistant_first_returns_400(self, client):
        r = client.post("/api/v1/ai/chat/completions", json={
            "messages": [{"role": "assistant", "content": "x"}],
        })
        assert r.status_code == 400

    def test_invalid_role_returns_400(self, client):
        r = client.post("/api/v1/ai/chat/completions", json={
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "function", "content": "x"},
            ],
        })
        assert r.status_code == 400


# ─── 비스트리밍 happy path ────────────────────────────────────────────────────

class TestNonStreamingInvoke:
    def test_200_with_openai_schema(self, client):
        fake_response_body = MagicMock()
        fake_response_body.read.return_value = json.dumps({
            "content": [{"type": "text", "text": "안녕하세요"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }).encode()

        fake_client = MagicMock()
        fake_client.invoke_model.return_value = {"body": fake_response_body}

        with patch.object(ai_router_mod.boto3, "client", return_value=fake_client):
            r = client.post("/api/v1/ai/chat/completions", json={
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "안녕"}],
                "stream": False,
            })

        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "chat.completion"
        assert data["model"] == "claude-sonnet-4-6"
        assert data["choices"][0]["message"]["content"] == "안녕하세요"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert data["usage"]["total_tokens"] == 15

    def test_bedrock_throttle_returns_429_openai_schema(self, client):
        import botocore.exceptions

        fake_client = MagicMock()
        fake_client.invoke_model.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "rate limit"}},
            "InvokeModel",
        )

        with patch.object(ai_router_mod.boto3, "client", return_value=fake_client):
            r = client.post("/api/v1/ai/chat/completions", json={
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            })

        assert r.status_code == 429
        body = r.json()
        assert body["error"]["type"] == "rate_limit_error"
        assert body["error"]["code"] == "ThrottlingException"


# ─── 스트리밍 happy path ──────────────────────────────────────────────────────

def _make_stream_event(anthropic_payload: dict) -> dict:
    return {"chunk": {"bytes": json.dumps(anthropic_payload).encode()}}


class TestStreaming:
    def test_sse_stream_happy_path(self, client):
        """정상 스트리밍: message_start → text_delta × 2 → message_delta → message_stop."""
        events = [
            _make_stream_event({"type": "message_start", "message": {"usage": {"input_tokens": 5}}}),
            _make_stream_event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "안녕"}}),
            _make_stream_event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "하세요"}}),
            _make_stream_event({
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 3},
            }),
            _make_stream_event({"type": "message_stop"}),
        ]

        fake_body = MagicMock()
        fake_body.__iter__.return_value = iter(events)
        fake_body.close = MagicMock()

        fake_client = MagicMock()
        fake_client.invoke_model_with_response_stream.return_value = {"body": fake_body}

        with patch.object(ai_router_mod.boto3, "client", return_value=fake_client):
            r = client.post("/api/v1/ai/chat/completions", json={
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })

        assert r.status_code == 200
        body_text = r.text
        # role delta + 2 content chunks + finish + [DONE]
        assert body_text.count("data: ") == 5
        assert "[DONE]" in body_text
        assert "안녕" in body_text
        assert "하세요" in body_text
        # Bedrock stream resource 해제 확인
        fake_body.close.assert_called_once()

    def test_stream_closes_body_on_disconnect(self):
        """★ CRITICAL REGRESSION: client disconnect 시 Bedrock stream이 즉시 close되어야 한다.

        _stream_openai_compat generator를 직접 호출하고, 두 번째 이터레이션부터
        is_disconnected()가 True를 반환하게 하여 루프가 조기 종료되는지 확인.
        Bedrock 토큰 과금 누수 방지용 (D2 Iron Law).
        """
        events = [
            _make_stream_event({"type": "message_start", "message": {"usage": {"input_tokens": 5}}}),
            _make_stream_event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "첫"}}),
            # 이 이후 이벤트들은 disconnect로 인해 소비되지 않아야 함
            _make_stream_event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "둘"}}),
            _make_stream_event({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "셋"}}),
            _make_stream_event({"type": "message_stop"}),
        ]

        fake_body = MagicMock()
        fake_body.__iter__.return_value = iter(events)
        fake_body.close = MagicMock()

        fake_client = MagicMock()
        fake_client.invoke_model_with_response_stream.return_value = {"body": fake_body}

        # request.is_disconnected를 점진 True로 모사:
        # 1번째(message_start) False → yield role delta
        # 2번째(content_block_delta text="첫") False → yield content
        # 3번째(content_block_delta text="둘") True → break
        disconnect_sequence = iter([False, False, True, True, True, True])

        async def fake_is_disconnected():
            try:
                return next(disconnect_sequence)
            except StopIteration:
                return True

        fake_request = MagicMock()
        fake_request.is_disconnected = fake_is_disconnected

        async def _consume():
            gen = ai_router_mod._stream_openai_compat(
                fake_request, fake_client, "global.anthropic.claude-sonnet-4-6",
                {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100},
                "TESTUSER01", "claude-sonnet-4-6",
            )
            chunks = []
            async for c in gen:
                chunks.append(c)
            return chunks

        chunks = asyncio.run(_consume())

        # body_stream.close()가 호출되어야 함 (핵심 regression 검사)
        fake_body.close.assert_called_once()

        # disconnect 이전에 yield된 chunk는 role delta + '첫' 2개
        # disconnect 이후 chunks는 생산되지 않음 (즉 '둘', '셋'는 없어야 함)
        all_text = b"".join(chunks).decode()
        assert "첫" in all_text
        assert "둘" not in all_text, "client disconnect 후에도 chunk가 yield됨 (D2 실패)"
        assert "셋" not in all_text
        # [DONE]도 emit되지 않아야 함 (정상 종료 아니므로)
        assert "[DONE]" not in all_text


# ─── Models 엔드포인트 ─────────────────────────────────────────────────────────

class TestModelsEndpoint:
    def test_list_returns_openai_schema(self, client):
        r = client.get("/api/v1/ai/models")
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "list"
        model_ids = {m["id"] for m in data["data"]}
        assert "claude-sonnet-4-6" in model_ids
        assert "claude-haiku-4-5" in model_ids
