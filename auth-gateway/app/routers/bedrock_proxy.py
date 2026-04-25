"""Bedrock AG Anthropic-compatible proxy endpoint — T20 Console Pod migration.

목적: Console Pod(claude-code-terminal)의 Bedrock 직접 호출을 이 엔드포인트로 전환.
     ANTHROPIC_BASE_URL=http://auth-gateway.platform.svc.cluster.local/v1

흐름:
  Console Pod → POST /v1/messages (Anthropic 호환) → Auth Gateway
             → JWT 검증 (bedrock_jwt 쿠키 또는 Bearer 헤더)
             → usage_event Redis Stream XADD (비동기)
             → AWS Bedrock InvokeModel 호출 (streaming 포함)
             → 응답 그대로 반환

모델 ID 매핑 (Anthropic model ID → Bedrock model ID):
  claude-sonnet-4-6           → global.anthropic.claude-sonnet-4-6
  claude-haiku-4-5-*          → global.anthropic.claude-haiku-4-5-20251001-v1:0
  claude-opus-4-6             → global.anthropic.claude-opus-4-6  (예약)

Status: T20 DRAFT — T2-APPLY + T11 완료 후 실 배포.
        현재는 엔드포인트 스켈레톤 + 매핑 테이블만 구현.
        실제 Bedrock InvokeModel 호출은 DEPLOY 시점에 활성화.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncGenerator

import boto3
import botocore.exceptions
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from prometheus_client import Counter

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.redis_client import get_redis
from app.core.security import get_current_user
from app.routers.admin import _check_user_quota

router = APIRouter(prefix="/v1", tags=["bedrock-proxy"])
logger = logging.getLogger(__name__)

publish_drop_counter = Counter(
    "bedrock_proxy_publish_drop_total",
    "stream:usage_events publish 실패 횟수 (silent drop 방지용)",
    ["reason"],
)

# ─── 모델 ID 매핑 (Anthropic model ID → Bedrock cross-region inference profile) ──
# Claude Code CLI가 전달하는 Anthropic model ID를 Bedrock 호환 ID로 변환한다.
# 참고: global.* prefix = cross-region inference profile (ap-northeast-2 기준)

MODEL_MAP: dict[str, str] = {
    # Claude Sonnet 4.6
    "claude-sonnet-4-6": "global.anthropic.claude-sonnet-4-6",
    "claude-sonnet-4-5": "us.anthropic.claude-sonnet-4-5-20251001-v1:0",
    # Claude Haiku 4.5
    "claude-haiku-4-5-20251001": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-haiku-4-5": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    # Claude Opus 4.6 (예약)
    "claude-opus-4-6": "global.anthropic.claude-opus-4-6",
    # 기본값: 매핑 없으면 그대로 전달 (Bedrock 측에서 validation)
}

DEFAULT_MODEL = "global.anthropic.claude-sonnet-4-6"
HAIKU_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def _resolve_model(anthropic_model_id: str, settings: Settings) -> str:
    """Anthropic model ID → Bedrock model ID 변환.

    1. MODEL_MAP 정확 매칭 → 변환
    2. 전달된 값이 이미 Bedrock prefix (global.*, us.*, ap.*) → 그대로 사용
    3. 매핑 없음 → settings.bedrock_sonnet_model (기본값)
    """
    if anthropic_model_id in MODEL_MAP:
        return MODEL_MAP[anthropic_model_id]
    if any(anthropic_model_id.startswith(p) for p in ("global.", "us.", "ap.", "eu.")):
        return anthropic_model_id
    # 접두사 포함 부분 매칭 (e.g. "claude-haiku-4-5-20251001-v1:0")
    for key, val in MODEL_MAP.items():
        if anthropic_model_id.startswith(key):
            return val
    logger.warning("Unknown model '%s' — fallback to default", anthropic_model_id)
    return settings.bedrock_sonnet_model or DEFAULT_MODEL


def _get_user_model_tier(db, username: str) -> str:
    """사용자 model_tier 조회. DB miss 시 기본값 'sonnet'."""
    from app.models.user import User

    user = (
        db.query(User)
        .filter(User.username == username.upper())
        .with_entities(User.model_tier)
        .first()
    )
    if user and user.model_tier:
        return user.model_tier
    return "sonnet"


def _apply_model_tier(resolved_model: str, tier: str, settings: Settings) -> str:
    """서버 사이드 모델 티어 정책 적용.

    'sonnet': 클라이언트 요청 모델 그대로 (기본)
    'haiku' : 항상 Haiku로 강제 다운그레이드 (비용 절감)
    'auto'  : 향후 확장 예약 — 현재는 'sonnet'과 동일
    """
    if tier == "haiku":
        haiku = settings.bedrock_haiku_model or HAIKU_MODEL
        if resolved_model != haiku:
            logger.info("model_tier=haiku override: %s → %s", resolved_model, haiku)
        return haiku
    return resolved_model


def _publish_usage_event(
    *,
    request_id: str,
    source: str,
    username: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cost_usd: float,
    cost_krw: int,
) -> None:
    """사용량 이벤트를 Redis Stream에 publish.

    silent drop 금지 — Redis 미가용/예외 시 publish_drop_counter 증가 + ERROR 로그.
    measurement spec(§5.2)의 drop counter 의무 사항.
    """
    from datetime import datetime, timezone

    try:
        r = get_redis()
        if not r:
            publish_drop_counter.labels(reason="redis_unavailable").inc()
            logger.error(
                "usage publish dropped: Redis unavailable. request_id=%s username=%s",
                request_id, username,
            )
            return

        r.xadd(
            "stream:usage_events",
            {
                "request_id": request_id,
                "source": source,
                "username": username,
                "model": model,
                "input_tokens": str(input_tokens),
                "output_tokens": str(output_tokens),
                "cache_creation_input_tokens": str(cache_creation_input_tokens),
                "cache_read_input_tokens": str(cache_read_input_tokens),
                "total_tokens": str(input_tokens + output_tokens),
                "cost_usd": f"{cost_usd:.6f}",
                "cost_krw": str(cost_krw),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
            maxlen=100_000,
            approximate=True,
        )
    except Exception as e:
        publish_drop_counter.labels(reason="exception").inc()
        logger.error(
            "usage publish failed: %s, request_id=%s username=%s",
            e, request_id, username,
        )


# ─── Anthropic-compatible /v1/messages endpoint ───────────────────────────────

@router.post("/messages")
async def messages(
    request: Request,
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """Anthropic Messages API 호환 프록시.

    Console Pod의 claude CLI가 `ANTHROPIC_BASE_URL`을 이 Auth Gateway로
    설정하면, 모든 LLM 호출이 이 엔드포인트를 거쳐 Bedrock로 전달된다.

    인증: get_current_user() — Bearer 헤더 또는 bedrock_jwt 쿠키.
    """
    username = current_user.get("sub", "unknown")

    # CP-20 Budget Gate: quota 초과 시 429 차단.
    # quota 미배정 사용자는 통과(세션 생성 시 정책 배정이 정식 경로).
    _db = SessionLocal()
    try:
        quota_info = _check_user_quota(_db, username)
        user_model_tier = _get_user_model_tier(_db, username)
    finally:
        _db.close()
    if quota_info and quota_info["is_exceeded"] and not quota_info["is_unlimited"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "token_quota_exceeded",
                "cost_limit_usd": quota_info["cost_limit_usd"],
                "current_usage_usd": quota_info["current_usage_usd"],
                "refresh_cycle": quota_info["refresh_cycle"],
                "cycle_start": quota_info["cycle_start"],
                "cycle_end": quota_info["cycle_end"],
            },
        )

    body = await request.json()

    model_input = body.get("model", "claude-sonnet-4-6")
    bedrock_model = _resolve_model(model_input, settings)
    bedrock_model = _apply_model_tier(bedrock_model, user_model_tier, settings)
    is_streaming = body.get("stream", False)

    # Bedrock 호환 요청 페이로드 구성
    bedrock_body = {
        "anthropic_version": body.get("anthropic_version", "bedrock-2023-05-31"),
        "messages": body.get("messages", []),
        "max_tokens": body.get("max_tokens", 4096),
    }
    if "system" in body:
        bedrock_body["system"] = body["system"]
    if "temperature" in body:
        bedrock_body["temperature"] = body["temperature"]
    if "top_p" in body:
        bedrock_body["top_p"] = body["top_p"]
    if "stop_sequences" in body:
        bedrock_body["stop_sequences"] = body["stop_sequences"]
    if "tools" in body:
        bedrock_body["tools"] = body["tools"]
    if "tool_choice" in body:
        bedrock_body["tool_choice"] = body["tool_choice"]

    region = settings.bedrock_region or "ap-northeast-2"
    bedrock = boto3.client("bedrock-runtime", region_name=region)

    try:
        if is_streaming:
            return StreamingResponse(
                _stream_bedrock(bedrock, bedrock_model, bedrock_body, username),
                media_type="text/event-stream",
            )
        else:
            return await _invoke_bedrock(bedrock, bedrock_model, bedrock_body, username)
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        logger.error("Bedrock invoke error: user=%s model=%s code=%s", username, bedrock_model, code)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Bedrock error: {code}",
        )


async def _invoke_bedrock(
    client, model_id: str, body: dict, username: str
) -> dict:
    """비스트리밍 Bedrock 호출 + usage 이벤트 발행."""
    import asyncio

    request_id = str(uuid.uuid4())
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
    input_tokens   = usage.get("input_tokens", 0)
    output_tokens  = usage.get("output_tokens", 0)
    cache_creation = usage.get("cache_creation_input_tokens", 0)
    cache_read     = usage.get("cache_read_input_tokens", 0)

    from app.core.pricing import KRW_RATE
    cost_usd = _estimate_cost_usd(
        model_id, input_tokens, output_tokens, cache_creation, cache_read,
    )
    cost_krw = int(cost_usd * KRW_RATE)

    _publish_usage_event(
        request_id=request_id,
        source="console-cli",
        username=username,
        model=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        cost_usd=cost_usd,
        cost_krw=cost_krw,
    )

    return result


async def _stream_bedrock(
    client, model_id: str, body: dict, username: str
) -> AsyncGenerator[bytes, None]:
    """스트리밍 Bedrock 호출 — Server-Sent Events 형식으로 전달."""
    import asyncio
    from app.core.pricing import KRW_RATE

    request_id = str(uuid.uuid4())
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

    input_tokens  = 0
    output_tokens = 0
    cache_creation = 0
    cache_read     = 0

    for event in response["body"]:
        chunk = event.get("chunk", {})
        if not chunk:
            continue
        raw = chunk.get("bytes", b"")
        if raw:
            data = json.loads(raw)
            if data.get("type") == "message_start":
                usage = data.get("message", {}).get("usage", {}) or {}
                input_tokens   += usage.get("input_tokens", 0)
                cache_creation += usage.get("cache_creation_input_tokens", 0)
                cache_read     += usage.get("cache_read_input_tokens", 0)
            elif data.get("type") == "message_delta":
                usage = data.get("usage", {}) or {}
                output_tokens  += usage.get("output_tokens", 0)
                cache_creation += usage.get("cache_creation_input_tokens", 0)
                cache_read     += usage.get("cache_read_input_tokens", 0)

            yield f"data: {json.dumps(data)}\n\n".encode()

    # 스트림 종료 후 사용량 이벤트 발행
    cost_usd = _estimate_cost_usd(
        model_id, input_tokens, output_tokens, cache_creation, cache_read,
    )
    cost_krw = int(cost_usd * KRW_RATE)
    _publish_usage_event(
        request_id=request_id,
        source="console-cli",
        username=username,
        model=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        cost_usd=cost_usd,
        cost_krw=cost_krw,
    )

    yield b"data: [DONE]\n\n"


def _estimate_cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> float:
    """모델별 4분기 토큰 비용 추정 (USD).

    pricing.py 단일 출처 사용 — 가격 변경 시 pricing.py만 수정하면 됨.
    cache_creation은 input의 1.25배, cache_read는 input의 0.10배 단가.
    """
    from app.core.pricing import get_price_table
    p = get_price_table(model_id)
    return (
        input_tokens   * p["input"]
        + output_tokens  * p["output"]
        + cache_creation * p["cache_creation"]
        + cache_read     * p["cache_read"]
    ) / 1_000_000
