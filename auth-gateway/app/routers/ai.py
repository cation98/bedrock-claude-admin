"""OpenAI-compatible AI endpoint (OnlyOffice AI plugin 전용).

설계: docs/plans/2026-04-12-onlyoffice-ai-integration-design.md (Approach B)
Eng Review (2026-04-12):
  D1: 직접 Bedrock (boto3) + bedrock_proxy 헬퍼 재사용 (DRY)
  D2: client disconnect 처리 — 스트리밍 루프 내 is_disconnected() 체크 → close()
  D3: 자체 OnlyOffice 플러그인 호출 경로

Endpoints:
  POST /api/v1/ai/chat/completions  — OpenAI schema chat completions (streaming/non-streaming)
  GET  /api/v1/ai/models            — 지원 모델 목록

인증: get_current_user (Bearer 또는 쿠키) — 기존 패턴 재사용.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

import boto3
import botocore.exceptions
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import Settings, get_settings
from app.core.security import get_current_user
from app.routers.bedrock_proxy import _resolve_model, _publish_usage_event, _estimate_cost_usd
from app.services.bedrock_adapter import (
    SUPPORTED_MODELS,
    anthropic_stream_event_to_openai_chunks,
    anthropic_to_openai_response,
    make_chunk_id,
    map_bedrock_error,
    new_stream_state,
    openai_to_anthropic_request,
)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])
logger = logging.getLogger(__name__)


@router.get("/models")
async def list_models(current_user: dict = Depends(get_current_user)):
    """OpenAI /v1/models 호환 — OnlyOffice 플러그인 드롭다운용."""
    return {"object": "list", "data": SUPPORTED_MODELS}


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """OpenAI /v1/chat/completions 호환 엔드포인트.

    OnlyOffice AI 플러그인 및 기타 OpenAI-compatible 클라이언트가 호출.
    내부적으로 Anthropic Messages schema로 변환 후 Bedrock InvokeModel 호출.

    Streaming(stream=true)은 Server-Sent Events 형식.
    client disconnect 감지 시 Bedrock 스트림을 즉시 중단해 과금 누수 방지 (D2).
    """
    username = current_user.get("sub", "unknown")

    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail={
            "error": {"message": "Invalid JSON body", "type": "invalid_request_error"}
        })

    # OpenAI → Anthropic 변환 (스키마 검증 포함)
    try:
        anthropic_body = openai_to_anthropic_request(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={
            "error": {"message": str(e), "type": "invalid_request_error"}
        })

    request_model = body.get("model", "claude-sonnet-4-6")
    bedrock_model = _resolve_model(request_model, settings)
    is_streaming = bool(body.get("stream", False))

    region = settings.bedrock_region or "ap-northeast-2"
    bedrock = boto3.client("bedrock-runtime", region_name=region)

    try:
        if is_streaming:
            return StreamingResponse(
                _stream_openai_compat(
                    request, bedrock, bedrock_model, anthropic_body, username, request_model
                ),
                media_type="text/event-stream",
            )
        # 비스트리밍
        anthropic_resp = await _invoke_non_streaming(
            bedrock, bedrock_model, anthropic_body, username
        )
        return JSONResponse(
            content=anthropic_to_openai_response(
                anthropic_resp,
                request_model=request_model,
                username=username,
            )
        )
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        logger.error(
            "Bedrock invoke error: user=%s model=%s code=%s",
            username, bedrock_model, code,
        )
        status_code, body_out = map_bedrock_error(code)
        return JSONResponse(status_code=status_code, content=body_out)


async def _invoke_non_streaming(
    client, model_id: str, body: dict, username: str
) -> dict:
    """비스트리밍 Bedrock InvokeModel 호출 + usage 이벤트 발행."""
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        ),
    )
    result = json.loads(response["body"].read())

    usage = result.get("usage", {}) or {}
    in_tok   = usage.get("input_tokens", 0)
    out_tok  = usage.get("output_tokens", 0)
    cache_cr = usage.get("cache_creation_input_tokens", 0)
    cache_rd = usage.get("cache_read_input_tokens", 0)
    from app.core.pricing import KRW_RATE
    cost = _estimate_cost_usd(model_id, in_tok, out_tok, cache_cr, cache_rd)
    _publish_usage_event(
        request_id=str(uuid.uuid4()),
        source="onlyoffice",
        username=username,
        model=model_id,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_creation_input_tokens=cache_cr,
        cache_read_input_tokens=cache_rd,
        cost_usd=cost,
        cost_krw=int(cost * KRW_RATE),
    )
    return result


async def _stream_openai_compat(
    request: Request,
    client,
    model_id: str,
    body: dict,
    username: str,
    request_model: str,
):
    """Bedrock streaming → OpenAI SSE chunks (with client disconnect handling).

    D2 Iron Law: 매 chunk 전송 전 request.is_disconnected() 체크.
    disconnect 감지 시 Bedrock response body를 close하고 "aborted" usage 이벤트 발행.
    """
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.invoke_model_with_response_stream(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        ),
    )
    body_stream = response["body"]

    chunk_id = make_chunk_id()
    state = new_stream_state()
    aborted = False

    try:
        for event in body_stream:
            # D2: client disconnect 검사
            if await request.is_disconnected():
                aborted = True
                logger.info(
                    "ai.chat.completions stream aborted by client: user=%s model=%s "
                    "input_tokens=%s output_tokens_so_far=%s",
                    username, model_id, state["input_tokens"], state["output_tokens"],
                )
                break

            chunk = event.get("chunk", {})
            if not chunk:
                continue
            raw = chunk.get("bytes", b"")
            if not raw:
                continue

            try:
                anthropic_event = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Malformed Bedrock chunk, skipped")
                continue

            sse_chunks = anthropic_stream_event_to_openai_chunks(
                anthropic_event,
                chunk_id=chunk_id,
                request_model=request_model,
                state=state,
            )
            for c in sse_chunks:
                yield c
    finally:
        # Bedrock 스트림 자원 해제 (disconnect/정상/예외 모두 포함)
        try:
            body_stream.close()
        except Exception:  # noqa: BLE001 — cleanup path
            pass

        # usage 기록 (aborted 포함 — 이미 소비된 토큰만 집계)
        from app.core.pricing import KRW_RATE
        cost = _estimate_cost_usd(
            model_id,
            state["input_tokens"],
            state["output_tokens"],
            state.get("cache_creation_input_tokens", 0),
            state.get("cache_read_input_tokens", 0),
        )
        _publish_usage_event(
            request_id=str(uuid.uuid4()),
            source="onlyoffice",
            username=username,
            model=model_id,
            input_tokens=state["input_tokens"],
            output_tokens=state["output_tokens"],
            cache_creation_input_tokens=state.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=state.get("cache_read_input_tokens", 0),
            cost_usd=cost,
            cost_krw=int(cost * KRW_RATE),
        )
        if aborted:
            logger.info(
                "ai.chat.completions aborted: user=%s tokens_billed=%s/%s",
                username, state["input_tokens"], state["output_tokens"],
            )
