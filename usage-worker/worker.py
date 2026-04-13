"""Usage Worker — Redis Stream consumer for token usage aggregation.

Stream: stream:usage_events
Consumer Group: usage-workers
Batch: 10 events OR 1-second timeout → UPSERT into token_usage_daily + token_usage_hourly

두 가지 producer 스키마를 모두 지원:

  [Pipelines / webchat 경로]  —  usage_emit_pipeline (openwebui-pipelines.yaml)
    user_id      str   Platform DB users.id (integer string) 또는 Open WebUI UUID
    username     str   사번 (email 추출 후 emit, 없으면 user_id 기반 DB 조회)
    source       str   "webchat"
    model        str   Bedrock model ID
    input_tokens str   (숫자 문자열)
    output_tokens str
    ts           str   Unix timestamp (recorded_at 없을 때 fallback)

  [Console Pod / bedrock_proxy 경로]  —  T20 bedrock_proxy.py
    username     str   사번 (JWT sub)
    model        str   Bedrock model ID
    input_tokens str
    output_tokens str
    total_tokens str
    cost_usd     str   (숫자 문자열)
    cost_krw     str
    recorded_at  str   ISO-8601 UTC
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [usage-worker] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ─── 설정 ─────────────────────────────────────────────────────────────────────

REDIS_URL = os.environ["REDIS_URL"]
DATABASE_URL = os.environ["DATABASE_URL"]

STREAM_KEY = "stream:usage_events"
CONSUMER_GROUP = "usage-workers"
CONSUMER_NAME = os.environ.get("HOSTNAME", "worker-0")

BATCH_SIZE = 10
BLOCK_MS = 1000
RECONNECT_DELAY = 5

# Dead consumer PEL 복구 설정
# - idle > STALE_IDLE_MS 인 pending 메시지는 dead consumer 소유로 간주해 self로 claim.
# - k8s Pod 종료 graceful timeout(기본 30s) + 여유 버퍼를 고려해 60s 기본값.
STALE_IDLE_MS = int(os.environ.get("USAGE_WORKER_STALE_IDLE_MS", 60_000))
CLAIM_INTERVAL_SEC = int(os.environ.get("USAGE_WORKER_CLAIM_INTERVAL_SEC", 30))
CLAIM_BATCH = 100


# ─── DB 연결 ──────────────────────────────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=2, max_overflow=2)
Session = sessionmaker(bind=engine)


# ─── Redis 초기화 ─────────────────────────────────────────────────────────────

def get_redis_client() -> redis.Redis:
    return redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
    )


def ensure_stream_and_group(r: redis.Redis) -> None:
    try:
        r.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
        logger.info("Consumer group '%s' created on '%s'", CONSUMER_GROUP, STREAM_KEY)
    except redis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.debug("Consumer group already exists — OK")
        else:
            raise


# ─── user_id → username 조회 (Pipelines 경로용) ──────────────────────────────

# 세션 내 간단한 메모리 캐시 (재시작 시 초기화 — 사용자 수 제한적이므로 충분)
_username_cache: dict[str, str] = {}


def _resolve_username(fields: dict) -> str | None:
    """이벤트 필드에서 사번(username) 추출.

    우선순위:
    1. `username` 필드 직접 사용 (bedrock_proxy / 최신 usage_emit 경로)
    2. `user_id`가 integer string → Platform DB `users.id` 기준 조회
    3. `user_id`가 이메일 패턴 → 도메인 앞 부분을 대문자로 변환
    4. `user_id`를 username으로 직접 사용 (fallback)
    """
    if fields.get("username"):
        return fields["username"]

    user_id = fields.get("user_id", "").strip()
    if not user_id:
        return None

    if user_id in _username_cache:
        return _username_cache[user_id]

    resolved: str | None = None

    # integer string → DB 조회 (Platform DB users.id)
    if user_id.isdigit():
        try:
            with Session() as session:
                row = session.execute(
                    text("SELECT username FROM users WHERE id = :uid"),
                    {"uid": int(user_id)},
                ).fetchone()
                if row:
                    resolved = row[0]
        except Exception as e:
            logger.warning("username lookup failed for user_id=%s: %s", user_id, e)

    # email 패턴 (e.g. "n1102359@skons.net") → 사번 추출
    if not resolved and "@" in user_id:
        resolved = user_id.split("@")[0].upper()

    # fallback: user_id 그대로 사용
    if not resolved:
        resolved = user_id

    _username_cache[user_id] = resolved
    return resolved


# ─── 이벤트 비용 추정 ──────────────────────────────────────────────────────────

def _estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """모델별 토큰 비용 추정 (USD, 2026-04 기준)."""
    if "haiku" in model_id:
        in_price, out_price = 0.80, 4.00
    elif "opus" in model_id:
        in_price, out_price = 15.00, 75.00
    else:
        in_price, out_price = 3.00, 15.00
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


# ─── 이벤트 파싱 ──────────────────────────────────────────────────────────────

def parse_event(fields: dict) -> dict | None:
    """Redis Stream 필드 딕셔너리 → 정규화된 이벤트 dict.

    Pipelines (webchat) 및 bedrock_proxy (console) 두 경로 모두 처리.
    """
    try:
        # ── username 결정 ──
        username = _resolve_username(fields)
        if not username:
            logger.warning("Event has no username/user_id — skip: %s", fields)
            return None

        # ── 타임스탬프 결정: recorded_at 우선, ts fallback ──
        if fields.get("recorded_at"):
            recorded_at = datetime.fromisoformat(fields["recorded_at"])
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        elif fields.get("ts"):
            recorded_at = datetime.fromtimestamp(int(fields["ts"]), tz=timezone.utc)
        else:
            recorded_at = datetime.now(timezone.utc)

        input_tokens = int(fields.get("input_tokens", 0))
        output_tokens = int(fields.get("output_tokens", 0))
        total_tokens = int(fields.get("total_tokens", 0)) or (input_tokens + output_tokens)

        # ── 비용: 직접 제공 우선, 없으면 모델 단가 기준 추정 ──
        model = fields.get("model", "unknown")
        if fields.get("cost_usd"):
            cost_usd = Decimal(fields["cost_usd"])
        else:
            cost_usd = Decimal(str(_estimate_cost_usd(model, input_tokens, output_tokens)))

        if fields.get("cost_krw"):
            cost_krw = int(fields["cost_krw"])
        else:
            cost_krw = int(float(cost_usd) * 1400)

        return {
            "username": username,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "cost_krw": cost_krw,
            "usage_date": recorded_at.date(),
            "slot": recorded_at.hour * 6 + recorded_at.minute // 10,
            "hour": recorded_at.hour,
        }
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("Event parse error: %s — fields=%s", e, fields)
        return None


# ─── DB UPSERT ────────────────────────────────────────────────────────────────

def upsert_daily(session, events: list[dict]) -> None:
    for ev in events:
        session.execute(
            text("""
                INSERT INTO token_usage_daily
                    (username, usage_date, input_tokens, output_tokens, total_tokens,
                     cost_usd, cost_krw, last_activity_at, created_at, updated_at)
                VALUES
                    (:username, :usage_date, :input_tokens, :output_tokens, :total_tokens,
                     :cost_usd, :cost_krw, NOW(), NOW(), NOW())
                ON CONFLICT (username, usage_date) DO UPDATE SET
                    input_tokens   = token_usage_daily.input_tokens  + EXCLUDED.input_tokens,
                    output_tokens  = token_usage_daily.output_tokens + EXCLUDED.output_tokens,
                    total_tokens   = token_usage_daily.total_tokens  + EXCLUDED.total_tokens,
                    cost_usd       = token_usage_daily.cost_usd      + EXCLUDED.cost_usd,
                    cost_krw       = token_usage_daily.cost_krw      + EXCLUDED.cost_krw,
                    last_activity_at = GREATEST(token_usage_daily.last_activity_at, NOW()),
                    updated_at     = NOW()
            """),
            {
                "username": ev["username"],
                "usage_date": ev["usage_date"],
                "input_tokens": ev["input_tokens"],
                "output_tokens": ev["output_tokens"],
                "total_tokens": ev["total_tokens"],
                "cost_usd": ev["cost_usd"],
                "cost_krw": ev["cost_krw"],
            },
        )


def upsert_hourly(session, events: list[dict]) -> None:
    for ev in events:
        session.execute(
            text("""
                INSERT INTO token_usage_hourly
                    (username, usage_date, hour, slot, input_tokens, output_tokens,
                     total_tokens, cost_usd, cost_krw)
                VALUES
                    (:username, :usage_date, :hour, :slot, :input_tokens, :output_tokens,
                     :total_tokens, :cost_usd, :cost_krw)
                ON CONFLICT ON CONSTRAINT uq_slot_user_date_slot DO UPDATE SET
                    input_tokens  = token_usage_hourly.input_tokens  + EXCLUDED.input_tokens,
                    output_tokens = token_usage_hourly.output_tokens + EXCLUDED.output_tokens,
                    total_tokens  = token_usage_hourly.total_tokens  + EXCLUDED.total_tokens,
                    cost_usd      = token_usage_hourly.cost_usd      + EXCLUDED.cost_usd,
                    cost_krw      = token_usage_hourly.cost_krw      + EXCLUDED.cost_krw
            """),
            {
                "username": ev["username"],
                "usage_date": ev["usage_date"],
                "hour": ev["hour"],
                "slot": ev["slot"],
                "input_tokens": ev["input_tokens"],
                "output_tokens": ev["output_tokens"],
                "total_tokens": ev["total_tokens"],
                "cost_usd": ev["cost_usd"],
                "cost_krw": ev["cost_krw"],
            },
        )


def process_batch(r: redis.Redis, message_ids: list[str], events: list[dict]) -> None:
    if not events:
        return
    with Session() as session:
        try:
            upsert_daily(session, events)
            upsert_hourly(session, events)
            session.commit()
            r.xack(STREAM_KEY, CONSUMER_GROUP, *message_ids)
            logger.info("Processed %d events, ACKed %d", len(events), len(message_ids))
        except Exception as e:
            session.rollback()
            logger.error("DB commit failed — no ACK, will retry: %s", e)
            raise


# ─── 메인 루프 ────────────────────────────────────────────────────────────────

def run() -> None:
    logger.info(
        "Usage worker starting: consumer=%s group=%s stream=%s batch=%d",
        CONSUMER_NAME, CONSUMER_GROUP, STREAM_KEY, BATCH_SIZE,
    )

    while True:
        try:
            r = get_redis_client()
            r.ping()
            ensure_stream_and_group(r)
            logger.info("Redis connected. Consuming...")
            _consume_loop(r)
        except Exception as e:
            logger.error("Worker error — reconnecting in %ds: %s", RECONNECT_DELAY, e)
            time.sleep(RECONNECT_DELAY)


def _consume_loop(r: redis.Redis) -> None:
    _recover_pending(r)
    last_claim_ts = time.monotonic()

    while True:
        # 주기적으로 dead consumer pending 메시지 claim (at-least-once 보장 강화)
        if time.monotonic() - last_claim_ts >= CLAIM_INTERVAL_SEC:
            try:
                _claim_stale_pending(r)
            except Exception as e:
                # claim 실패는 전체 소비 차단하지 않음 — 다음 주기에 재시도
                logger.warning("Stale claim failed (will retry): %s", e)
            last_claim_ts = time.monotonic()

        results = r.xreadgroup(
            CONSUMER_GROUP,
            CONSUMER_NAME,
            {STREAM_KEY: ">"},
            count=BATCH_SIZE,
            block=BLOCK_MS,
        )

        if not results:
            continue

        message_ids = []
        events = []
        for _stream, messages in results:
            for msg_id, fields in messages:
                ev = parse_event(fields)
                if ev:
                    events.append(ev)
                    message_ids.append(msg_id)
                else:
                    r.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)
                    logger.warning("Bad event ACKed without processing: id=%s", msg_id)

        if message_ids:
            process_batch(r, message_ids, events)


def _recover_pending(r: redis.Redis, max_recover: int = 100) -> None:
    """이 consumer 자신의 PEL을 읽어 재처리 — 재시작 직후 1회 호출.

    XREADGROUP with id="0"은 자기 consumer의 pending entry만 반환 (redis-py 기준).
    다른 consumer(특히 죽은 pod)의 pending은 _claim_stale_pending이 담당.
    """
    results = r.xreadgroup(
        CONSUMER_GROUP,
        CONSUMER_NAME,
        {STREAM_KEY: "0"},
        count=max_recover,
    )
    if results:
        for _stream, messages in results:
            if not messages:
                break
            message_ids = []
            events = []
            for msg_id, fields in messages:
                ev = parse_event(fields)
                if ev:
                    events.append(ev)
                    message_ids.append(msg_id)
            if message_ids:
                process_batch(r, message_ids, events)
        logger.info("Pending recovery complete")


def _claim_stale_pending(r: redis.Redis) -> int:
    """Dead consumer의 pending 메시지를 self 소유로 transfer 후 처리.

    XAUTOCLAIM(Redis 6.2+)로 idle > STALE_IDLE_MS 인 PEL 엔트리를 cursor 기반
    순회하며 자신에게 귀속. 재시작 불가한 dead consumer(삭제된 Pod)가 남긴
    고아 메시지를 복구하여 at-least-once 보장을 유지한다.

    반환: claim 후 성공 처리된 메시지 수.
    """
    claimed_total = 0
    cursor = "0-0"
    while True:
        result = r.xautoclaim(
            STREAM_KEY,
            CONSUMER_GROUP,
            CONSUMER_NAME,
            min_idle_time=STALE_IDLE_MS,
            start_id=cursor,
            count=CLAIM_BATCH,
        )
        # redis-py 반환: (next_cursor, [(msg_id, fields), ...], [deleted_ids])
        if isinstance(result, tuple) and len(result) == 3:
            cursor, messages, _deleted = result
        else:
            # 구버전 호환: (next_cursor, messages)
            cursor, messages = result[0], result[1]

        if not messages:
            break

        message_ids: list[str] = []
        events: list[dict] = []
        for msg_id, fields in messages:
            ev = parse_event(fields)
            if ev:
                events.append(ev)
                message_ids.append(msg_id)
            else:
                # parse 실패 — 재처리해도 실패하므로 ACK하여 PEL 제거
                r.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)
                logger.warning("Stale bad event ACKed without processing: id=%s", msg_id)

        if message_ids:
            process_batch(r, message_ids, events)
            claimed_total += len(message_ids)

        # cursor "0-0" = 더 스캔할 엔트리 없음
        if cursor in ("0-0", b"0-0"):
            break

    if claimed_total:
        logger.info("Claimed %d stale messages from dead consumers", claimed_total)
    return claimed_total


if __name__ == "__main__":
    run()
