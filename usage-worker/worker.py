"""Usage Worker — Redis Stream consumer for token usage aggregation.

Stream: stream:usage_events
Consumer Group: usage-workers
Batch: 10 events OR 1-second timeout → UPSERT into token_usage_daily + token_usage_hourly

Event schema (XADD fields):
  username     str   사번 (e.g. N1102359)
  model        str   Bedrock model ID
  input_tokens int
  output_tokens int
  total_tokens int
  cost_usd     float
  cost_krw     int
  recorded_at  ISO-8601 UTC string

엔드포인트는 devops T2-APPLY 완료 후 REDIS_URL 환경변수로 주입된다.
T2 전까지는 이 파일을 준비 상태로 유지 — 실제 배포는 T2 완료 시점.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, date, timezone
from decimal import Decimal

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [usage-worker] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ─── 설정 ─────────────────────────────────────────────────────────────────────

REDIS_URL = os.environ["REDIS_URL"]           # 필수: devops T2-APPLY 후 주입
DATABASE_URL = os.environ["DATABASE_URL"]     # 필수: platform RDS

STREAM_KEY = "stream:usage_events"
CONSUMER_GROUP = "usage-workers"
CONSUMER_NAME = os.environ.get("HOSTNAME", "worker-0")  # K8s Pod hostname

BATCH_SIZE = 10       # 최대 이벤트 개수 (배치 단위)
BLOCK_MS = 1000       # 1초 대기 후 빈 배치 flush
RECONNECT_DELAY = 5   # Redis 연결 실패 시 재시도 간격 (초)


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
    """Stream과 Consumer Group이 없으면 생성한다."""
    try:
        # Stream이 없으면 mkstream=True로 생성
        r.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
        logger.info("Consumer group '%s' created on '%s'", CONSUMER_GROUP, STREAM_KEY)
    except redis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.debug("Consumer group already exists — OK")
        else:
            raise


# ─── 이벤트 처리 ──────────────────────────────────────────────────────────────

def parse_event(fields: dict) -> dict | None:
    """Redis Stream 필드 딕셔너리 → 정규화된 이벤트 dict."""
    try:
        recorded_at = datetime.fromisoformat(fields["recorded_at"])
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        return {
            "username": fields["username"],
            "model": fields.get("model", "unknown"),
            "input_tokens": int(fields.get("input_tokens", 0)),
            "output_tokens": int(fields.get("output_tokens", 0)),
            "total_tokens": int(fields.get("total_tokens", 0)),
            "cost_usd": Decimal(fields.get("cost_usd", "0")),
            "cost_krw": int(fields.get("cost_krw", 0)),
            "usage_date": recorded_at.date(),
            "slot": recorded_at.hour * 6 + recorded_at.minute // 10,  # 0-143
            "hour": recorded_at.hour,  # 레거시 호환
        }
    except (KeyError, ValueError) as e:
        logger.warning("Event parse error: %s — fields=%s", e, fields)
        return None


def upsert_daily(session, events: list[dict]) -> None:
    """token_usage_daily UPSERT (username, usage_date 기준 누적)."""
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
    """token_usage_hourly UPSERT (username, usage_date, slot 기준 누적)."""
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
    """DB UPSERT + ACK — 하나의 트랜잭션으로 묶음."""
    if not events:
        return
    with Session() as session:
        try:
            upsert_daily(session, events)
            upsert_hourly(session, events)
            session.commit()
            # ACK는 DB commit 성공 후만 수행 — at-least-once 보장
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
    """XREADGROUP 기반 배치 소비 루프."""
    # 미처리 Pending 메시지 먼저 재처리 (이전 worker 장애 복구)
    _recover_pending(r)

    while True:
        # BLOCK: 1초 대기, 최대 BATCH_SIZE 건 수신
        results = r.xreadgroup(
            CONSUMER_GROUP,
            CONSUMER_NAME,
            {STREAM_KEY: ">"},  # ">" = 새 메시지만
            count=BATCH_SIZE,
            block=BLOCK_MS,
        )

        if not results:
            continue  # 타임아웃 — 다음 iteration

        message_ids = []
        events = []
        for _stream, messages in results:
            for msg_id, fields in messages:
                ev = parse_event(fields)
                if ev:
                    events.append(ev)
                    message_ids.append(msg_id)
                else:
                    # 파싱 불가 메시지: 즉시 ACK (dead letter 방지)
                    r.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)
                    logger.warning("Bad event ACKed without processing: id=%s", msg_id)

        if message_ids:
            process_batch(r, message_ids, events)


def _recover_pending(r: redis.Redis, max_recover: int = 100) -> None:
    """이전 worker가 미처리한 Pending 메시지 재처리 (장애 복구).

    id="0" 로 XREADGROUP 하면 자신의 PEL에서 미확인 메시지를 재수신한다.
    """
    results = r.xreadgroup(
        CONSUMER_GROUP,
        CONSUMER_NAME,
        {STREAM_KEY: "0"},  # "0" = 이 consumer의 미처리 메시지
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


if __name__ == "__main__":
    run()
