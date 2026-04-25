"""OpenAI chat.completion ↔ Anthropic Messages schema 변환 어댑터.

OnlyOffice AI 플러그인 및 OpenAI-compatible 클라이언트가 호출하는
`/api/v1/ai/chat/completions`을 내부적으로 Bedrock Anthropic Messages API로 전달하기 위한 번역 계층.

설계 참고: docs/plans/2026-04-12-onlyoffice-ai-integration-design.md (Approach B)
Eng Review 결정:
  D1: LiteLLM 미사용 → boto3 Converse 직접이 아닌 `bedrock_proxy._resolve_model`
      재사용 + Anthropic Messages schema 경유 (기존 bedrock_proxy.py 파이프라인 재활용).
  D2: client disconnect 처리 필수 → ai.py router에서 처리.
  D3: 자체 OO 플러그인 작성 → 이 어댑터는 그 플러그인과 공용 OpenAI 호환 API.

변환 범위:
  - OpenAI messages[] → Anthropic system + messages[] (system 메시지 분리)
  - OpenAI chat.completion.chunk ← Anthropic message_delta/content_block_delta
  - finish_reason: Anthropic stop_reason → OpenAI finish_reason
  - error: Bedrock/ClientError → OpenAI error schema

스키마 단순화 원칙 (MVP):
  - tools/tool_choice, function calling, vision, log_probs 미지원
    (사용자 현 wedge: 요약/번역/교정/보고서 초안만 — 텍스트 in/out)
  - 필요 시 추후 확장
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from app.routers.bedrock_proxy import _publish_usage_event  # noqa: F401 (patch target)

logger = logging.getLogger(__name__)


# ─── Error 매핑 ────────────────────────────────────────────────────────────────

_BEDROCK_ERROR_MAP: dict[str, tuple[int, str]] = {
    # Bedrock ClientError code → (HTTP status, OpenAI error.type)
    "ThrottlingException": (429, "rate_limit_error"),
    "TooManyRequestsException": (429, "rate_limit_error"),
    "ServiceQuotaExceededException": (429, "rate_limit_error"),
    "ValidationException": (400, "invalid_request_error"),
    "AccessDeniedException": (403, "authentication_error"),
    "ResourceNotFoundException": (404, "invalid_request_error"),
    "ModelNotReadyException": (503, "api_error"),
    "ModelTimeoutException": (504, "api_error"),
    "ModelErrorException": (502, "api_error"),
    "ModelStreamErrorException": (502, "api_error"),
    "InternalServerException": (500, "api_error"),
}


def map_bedrock_error(code: str) -> tuple[int, dict]:
    """Bedrock ClientError code를 (HTTP status, OpenAI error body)로 매핑."""
    status_code, err_type = _BEDROCK_ERROR_MAP.get(code, (502, "api_error"))
    return status_code, {
        "error": {
            "message": f"Bedrock upstream error: {code}",
            "type": err_type,
            "code": code,
        }
    }


# ─── 요청 변환 (OpenAI → Anthropic) ───────────────────────────────────────────

def openai_to_anthropic_request(openai_body: dict) -> dict:
    """OpenAI chat.completions 요청 바디를 Anthropic Messages 요청 바디로 변환.

    - system 메시지(들)는 Anthropic의 `system` 필드로 합성 (OpenAI는 messages[0]에 위치)
    - 나머지는 role=user/assistant 순으로 보존
    - content가 str만이라고 가정 (멀티모달/tool use 비지원 — 현 wedge 범위)
    - 반환 dict는 bedrock_proxy._invoke_bedrock / _stream_bedrock에 전달 가능한 형태
    """
    messages_in = openai_body.get("messages") or []
    if not messages_in:
        raise ValueError("messages[] must not be empty")

    system_parts: list[str] = []
    converted: list[dict] = []

    for idx, m in enumerate(messages_in):
        role = m.get("role")
        content = m.get("content", "")
        if not isinstance(content, str):
            # OpenAI는 content에 list(blocks)를 허용하나 wedge에서는 문자열만.
            # list 이면 text block만 concat
            if isinstance(content, list):
                content = "".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            else:
                content = str(content)

        if role == "system":
            system_parts.append(content)
            continue
        if role not in ("user", "assistant"):
            raise ValueError(f"messages[{idx}].role must be system|user|assistant, got {role!r}")
        converted.append({"role": role, "content": content})

    if not converted:
        raise ValueError("messages[] must contain at least one user/assistant message")
    if converted[0]["role"] != "user":
        raise ValueError("first non-system message must have role=user")

    anthropic_body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": converted,
        "max_tokens": int(openai_body.get("max_tokens") or 4096),
    }
    if system_parts:
        anthropic_body["system"] = "\n\n".join(system_parts)

    # 선택 파라미터 패스스루
    if "temperature" in openai_body:
        anthropic_body["temperature"] = openai_body["temperature"]
    if "top_p" in openai_body:
        anthropic_body["top_p"] = openai_body["top_p"]
    if "stop" in openai_body:
        stop = openai_body["stop"]
        anthropic_body["stop_sequences"] = stop if isinstance(stop, list) else [stop]

    return anthropic_body


# ─── 응답 변환 (Anthropic → OpenAI) ───────────────────────────────────────────

# Anthropic stop_reason → OpenAI finish_reason
_FINISH_MAP: dict[str, str] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",  # wedge에서 미사용이나 매핑은 존재
}


def _map_finish_reason(stop_reason: str | None) -> str:
    return _FINISH_MAP.get(stop_reason or "", "stop")


def anthropic_to_openai_response(
    anthropic_resp: dict,
    *,
    request_model: str,
    username: str,
) -> dict:
    """비스트리밍 Anthropic Messages 응답 → OpenAI chat.completion 응답.

    + Redis stream:usage_events에 publish (source='onlyoffice', request_id 자동 생성).
    """
    from app.routers.bedrock_proxy import _estimate_cost_usd, MODEL_MAP
    from app.core.pricing import KRW_RATE

    content_blocks = anthropic_resp.get("content") or []
    text = "".join(
        b.get("text", "") for b in content_blocks if b.get("type") == "text"
    )
    usage = anthropic_resp.get("usage") or {}

    input_tokens   = usage.get("input_tokens", 0)
    output_tokens  = usage.get("output_tokens", 0)
    cache_creation = usage.get("cache_creation_input_tokens", 0)
    cache_read     = usage.get("cache_read_input_tokens", 0)

    bedrock_model = MODEL_MAP.get(request_model, request_model)
    cost_usd = _estimate_cost_usd(
        bedrock_model, input_tokens, output_tokens, cache_creation, cache_read,
    )
    cost_krw = int(cost_usd * KRW_RATE)

    _publish_usage_event(
        request_id=str(uuid.uuid4()),
        source="onlyoffice",
        username=username,
        model=bedrock_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        cost_usd=cost_usd,
        cost_krw=cost_krw,
    )

    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": _map_finish_reason(anthropic_resp.get("stop_reason")),
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


def _sse(data: dict | str) -> bytes:
    """OpenAI SSE 포맷으로 인코딩. str이면 [DONE] 같은 control 신호."""
    if isinstance(data, str):
        return f"data: {data}\n\n".encode()
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def anthropic_stream_event_to_openai_chunks(
    event: dict,
    *,
    chunk_id: str,
    request_model: str,
    state: dict,
) -> list[bytes]:
    """Anthropic 스트림 이벤트 1개 → OpenAI SSE chunk bytes list (0~여러 개).

    state는 스트림 내 누적 상태(usage 등)를 보존하기 위한 mutable dict.
    state["input_tokens"], state["output_tokens"], state["stop_reason"]가
    업데이트된다.

    Anthropic 스트림 이벤트 예시:
      {"type":"message_start", "message":{"usage":{"input_tokens":N}}}
      {"type":"content_block_start", "index":0, "content_block":{...}}
      {"type":"content_block_delta", "index":0, "delta":{"type":"text_delta","text":"..."}}
      {"type":"content_block_stop", "index":0}
      {"type":"message_delta", "delta":{"stop_reason":"end_turn"}, "usage":{"output_tokens":N}}
      {"type":"message_stop"}
    """
    etype = event.get("type")
    chunks: list[bytes] = []
    now = int(time.time())

    def _base_chunk() -> dict:
        return {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": now,
            "model": request_model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": None,
                }
            ],
        }

    if etype == "message_start":
        usage = event.get("message", {}).get("usage", {})
        state["input_tokens"] = state.get("input_tokens", 0) + usage.get("input_tokens", 0)
        state["cache_creation_input_tokens"] = state.get("cache_creation_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        state["cache_read_input_tokens"] = state.get("cache_read_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
        # 첫 chunk: role delta
        ch = _base_chunk()
        ch["choices"][0]["delta"] = {"role": "assistant", "content": ""}
        chunks.append(_sse(ch))

    elif etype == "content_block_delta":
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            text = delta.get("text", "")
            if text:
                ch = _base_chunk()
                ch["choices"][0]["delta"] = {"content": text}
                chunks.append(_sse(ch))

    elif etype == "message_delta":
        # usage 누적 + stop_reason 캡처
        usage = event.get("usage") or {}
        state["output_tokens"] = state.get("output_tokens", 0) + usage.get("output_tokens", 0)
        state["cache_creation_input_tokens"] = state.get("cache_creation_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        state["cache_read_input_tokens"] = state.get("cache_read_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
        delta = event.get("delta") or {}
        if delta.get("stop_reason"):
            state["stop_reason"] = delta["stop_reason"]

    elif etype == "message_stop":
        # 최종 finish_reason chunk + [DONE]
        ch = _base_chunk()
        ch["choices"][0]["finish_reason"] = _map_finish_reason(
            state.get("stop_reason")
        )
        chunks.append(_sse(ch))
        chunks.append(_sse("[DONE]"))

    # 다른 이벤트 타입(content_block_start/stop, ping 등)은 OpenAI 스트림에 대응물 없음
    return chunks


def new_stream_state() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "stop_reason": None,
    }


def make_chunk_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


# ─── 사용 가능한 모델 목록 ────────────────────────────────────────────────────

# OnlyOffice 플러그인 드롭다운에서 선택 가능한 사용자-페이싱 모델 목록.
# 실제 Bedrock 모델 ID는 bedrock_proxy.MODEL_MAP이 결정한다.
SUPPORTED_MODELS: list[dict] = [
    {
        "id": "claude-sonnet-4-6",
        "object": "model",
        "owned_by": "anthropic-via-bedrock",
        "description": "Claude Sonnet 4.6 (기본, 균형)",
    },
    {
        "id": "claude-haiku-4-5",
        "object": "model",
        "owned_by": "anthropic-via-bedrock",
        "description": "Claude Haiku 4.5 (저비용, 빠른 응답)",
    },
]
