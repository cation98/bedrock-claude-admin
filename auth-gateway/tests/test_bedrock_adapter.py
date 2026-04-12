"""bedrock_adapter.py 단위 테스트.

커버리지 다이어그램 기준 (docs/plans/2026-04-12-onlyoffice-ai-integration-test-plan.md):
  - openai_to_anthropic_request: user/assistant/system/multi-turn/error
  - anthropic_to_openai_response: text content + usage + finish_reason
  - anthropic_stream_event_to_openai_chunks: message_start/content_block_delta/message_delta/message_stop
  - map_bedrock_error: Throttling/Validation/ModelError/Unknown
"""

import json

import pytest

from app.services import bedrock_adapter as A


# ─── openai_to_anthropic_request ──────────────────────────────────────────────

class TestOpenAIToAnthropic:
    def test_simple_user_message(self):
        body = A.openai_to_anthropic_request({
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "안녕"}],
        })
        assert body["messages"] == [{"role": "user", "content": "안녕"}]
        assert body["max_tokens"] == 4096
        assert "system" not in body

    def test_system_message_hoisted(self):
        body = A.openai_to_anthropic_request({
            "messages": [
                {"role": "system", "content": "너는 한국어 전문가야"},
                {"role": "user", "content": "요약해줘"},
            ],
        })
        assert body["system"] == "너는 한국어 전문가야"
        assert body["messages"] == [{"role": "user", "content": "요약해줘"}]

    def test_multiple_system_messages_concatenated(self):
        body = A.openai_to_anthropic_request({
            "messages": [
                {"role": "system", "content": "규칙1"},
                {"role": "system", "content": "규칙2"},
                {"role": "user", "content": "시작"},
            ],
        })
        assert body["system"] == "규칙1\n\n규칙2"

    def test_multi_turn_user_assistant(self):
        body = A.openai_to_anthropic_request({
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "안녕하세요"},
                {"role": "user", "content": "요약"},
            ],
        })
        assert len(body["messages"]) == 3
        assert body["messages"][1]["role"] == "assistant"

    def test_max_tokens_passthrough(self):
        body = A.openai_to_anthropic_request({
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 512,
        })
        assert body["max_tokens"] == 512

    def test_stop_string_to_list(self):
        body = A.openai_to_anthropic_request({
            "messages": [{"role": "user", "content": "hi"}],
            "stop": "END",
        })
        assert body["stop_sequences"] == ["END"]

    def test_stop_list_passthrough(self):
        body = A.openai_to_anthropic_request({
            "messages": [{"role": "user", "content": "hi"}],
            "stop": ["END", "STOP"],
        })
        assert body["stop_sequences"] == ["END", "STOP"]

    def test_temperature_top_p_passthrough(self):
        body = A.openai_to_anthropic_request({
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.3,
            "top_p": 0.9,
        })
        assert body["temperature"] == 0.3
        assert body["top_p"] == 0.9

    def test_content_as_text_blocks_concatenated(self):
        """OpenAI가 content를 list[{type:text,text:...}]로 보내도 문자열로 합성."""
        body = A.openai_to_anthropic_request({
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "첫 번째. "},
                    {"type": "text", "text": "두 번째."},
                ],
            }],
        })
        assert body["messages"][0]["content"] == "첫 번째. 두 번째."

    # Error cases
    def test_empty_messages_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            A.openai_to_anthropic_request({"messages": []})

    def test_missing_messages_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            A.openai_to_anthropic_request({})

    def test_only_system_messages_raises(self):
        with pytest.raises(ValueError, match="at least one user"):
            A.openai_to_anthropic_request({
                "messages": [{"role": "system", "content": "x"}],
            })

    def test_assistant_first_raises(self):
        with pytest.raises(ValueError, match="first.*role=user"):
            A.openai_to_anthropic_request({
                "messages": [{"role": "assistant", "content": "x"}],
            })

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="must be system"):
            A.openai_to_anthropic_request({
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "function", "content": "x"},
                ],
            })


# ─── anthropic_to_openai_response ─────────────────────────────────────────────

class TestAnthropicToOpenAIResponse:
    def test_basic_response(self):
        resp = A.anthropic_to_openai_response(
            {
                "content": [{"type": "text", "text": "안녕하세요"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
            request_model="claude-sonnet-4-6",
        )
        assert resp["object"] == "chat.completion"
        assert resp["model"] == "claude-sonnet-4-6"
        assert resp["choices"][0]["message"]["content"] == "안녕하세요"
        assert resp["choices"][0]["finish_reason"] == "stop"
        assert resp["usage"] == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    def test_max_tokens_finish_reason(self):
        resp = A.anthropic_to_openai_response(
            {
                "content": [{"type": "text", "text": "부분 응답"}],
                "stop_reason": "max_tokens",
                "usage": {"input_tokens": 10, "output_tokens": 100},
            },
            request_model="claude-haiku-4-5",
        )
        assert resp["choices"][0]["finish_reason"] == "length"

    def test_missing_usage_defaults_zero(self):
        resp = A.anthropic_to_openai_response(
            {"content": [{"type": "text", "text": "x"}]},
            request_model="claude-sonnet-4-6",
        )
        assert resp["usage"]["prompt_tokens"] == 0
        assert resp["usage"]["completion_tokens"] == 0

    def test_multi_block_text_concatenated(self):
        resp = A.anthropic_to_openai_response(
            {
                "content": [
                    {"type": "text", "text": "첫 "},
                    {"type": "text", "text": "둘째"},
                ],
                "stop_reason": "end_turn",
            },
            request_model="claude-sonnet-4-6",
        )
        assert resp["choices"][0]["message"]["content"] == "첫 둘째"


# ─── Streaming: anthropic_stream_event_to_openai_chunks ───────────────────────

def _parse_sse(chunk_bytes: bytes) -> str | dict:
    """SSE 'data: ...\n\n' 파싱. [DONE]은 str, 그 외는 dict."""
    assert chunk_bytes.startswith(b"data: ")
    assert chunk_bytes.endswith(b"\n\n")
    payload = chunk_bytes[len(b"data: "):-2].decode()
    if payload == "[DONE]":
        return payload
    return json.loads(payload)


class TestStreamEventConversion:
    def test_message_start_emits_role_delta(self):
        state = A.new_stream_state()
        chunks = A.anthropic_stream_event_to_openai_chunks(
            {"type": "message_start", "message": {"usage": {"input_tokens": 42}}},
            chunk_id="chatcmpl-X",
            request_model="claude-sonnet-4-6",
            state=state,
        )
        assert len(chunks) == 1
        payload = _parse_sse(chunks[0])
        assert payload["choices"][0]["delta"] == {"role": "assistant", "content": ""}
        assert state["input_tokens"] == 42

    def test_content_block_delta_emits_content(self):
        state = A.new_stream_state()
        chunks = A.anthropic_stream_event_to_openai_chunks(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "안녕"}},
            chunk_id="chatcmpl-X",
            request_model="claude-sonnet-4-6",
            state=state,
        )
        assert len(chunks) == 1
        payload = _parse_sse(chunks[0])
        assert payload["choices"][0]["delta"] == {"content": "안녕"}

    def test_content_block_delta_empty_skipped(self):
        state = A.new_stream_state()
        chunks = A.anthropic_stream_event_to_openai_chunks(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ""}},
            chunk_id="chatcmpl-X",
            request_model="claude-sonnet-4-6",
            state=state,
        )
        assert chunks == []

    def test_message_delta_accumulates_usage(self):
        state = A.new_stream_state()
        chunks = A.anthropic_stream_event_to_openai_chunks(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 17},
            },
            chunk_id="chatcmpl-X",
            request_model="claude-sonnet-4-6",
            state=state,
        )
        assert chunks == []  # message_delta는 chunk 생성 안 함
        assert state["output_tokens"] == 17
        assert state["stop_reason"] == "end_turn"

    def test_message_stop_emits_finish_and_done(self):
        state = A.new_stream_state()
        state["stop_reason"] = "max_tokens"
        chunks = A.anthropic_stream_event_to_openai_chunks(
            {"type": "message_stop"},
            chunk_id="chatcmpl-X",
            request_model="claude-sonnet-4-6",
            state=state,
        )
        assert len(chunks) == 2
        final = _parse_sse(chunks[0])
        done = _parse_sse(chunks[1])
        assert final["choices"][0]["finish_reason"] == "length"
        assert done == "[DONE]"

    def test_unknown_event_ignored(self):
        state = A.new_stream_state()
        chunks = A.anthropic_stream_event_to_openai_chunks(
            {"type": "ping"},
            chunk_id="chatcmpl-X",
            request_model="claude-sonnet-4-6",
            state=state,
        )
        assert chunks == []


# ─── Error mapping ────────────────────────────────────────────────────────────

class TestErrorMapping:
    def test_throttling_429_rate_limit(self):
        status, body = A.map_bedrock_error("ThrottlingException")
        assert status == 429
        assert body["error"]["type"] == "rate_limit_error"
        assert body["error"]["code"] == "ThrottlingException"

    def test_validation_400_invalid_request(self):
        status, body = A.map_bedrock_error("ValidationException")
        assert status == 400
        assert body["error"]["type"] == "invalid_request_error"

    def test_model_error_502_api_error(self):
        status, body = A.map_bedrock_error("ModelErrorException")
        assert status == 502
        assert body["error"]["type"] == "api_error"

    def test_unknown_defaults_to_502_api_error(self):
        status, body = A.map_bedrock_error("SomeNewCodeTBD")
        assert status == 502
        assert body["error"]["type"] == "api_error"
        assert body["error"]["code"] == "SomeNewCodeTBD"


# ─── Helpers ───────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_chunk_id_format(self):
        cid = A.make_chunk_id()
        assert cid.startswith("chatcmpl-")
        assert len(cid) == len("chatcmpl-") + 24

    def test_new_stream_state_defaults(self):
        state = A.new_stream_state()
        assert state["input_tokens"] == 0
        assert state["output_tokens"] == 0
        assert state["stop_reason"] is None

    def test_supported_models_include_sonnet_haiku(self):
        ids = {m["id"] for m in A.SUPPORTED_MODELS}
        assert "claude-sonnet-4-6" in ids
        assert "claude-haiku-4-5" in ids
