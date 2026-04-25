"""토큰 사용량 일별/시간별 누적 + 이벤트 dedupe 테이블."""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, BigInteger, String, Date, DateTime, Numeric,
    UniqueConstraint, Index,
)

from app.core.database import Base


class TokenUsageDaily(Base):
    __tablename__ = "token_usage_daily"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    username        = Column(String(50), nullable=False)
    user_name       = Column(String(100))
    usage_date      = Column(Date, nullable=False)
    model_id        = Column(String(100), nullable=False, default="unknown")
    # billable input only — cache_* 미포함 (double-charge 방지)
    input_tokens    = Column(BigInteger, default=0)
    output_tokens   = Column(BigInteger, default=0)
    cache_creation_input_tokens = Column(BigInteger, default=0)
    cache_read_input_tokens     = Column(BigInteger, default=0)
    total_tokens    = Column(BigInteger, default=0)  # input + output (cache 제외)
    cost_usd        = Column(Numeric(12, 6), default=0)
    cost_krw        = Column(Integer, default=0)
    session_minutes = Column(Integer, default=0)
    last_activity_at = Column(DateTime(timezone=True))
    created_at      = Column(DateTime(timezone=True),
                             default=lambda: datetime.now(timezone.utc))
    updated_at      = Column(DateTime(timezone=True),
                             default=lambda: datetime.now(timezone.utc),
                             onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("username", "usage_date", "model_id",
                         name="uq_user_date_model"),
        Index("ix_usage_date_model", "usage_date", "model_id"),
        Index("ix_username_usage_date", "username", "usage_date"),
    )


class TokenUsageHourly(Base):
    """10분 단위 토큰 사용량 — 스파크라인 차트용.

    slot: 0-143 (24h × 6 slots/h = 144 slots per day)
    slot 계산: hour * 6 + minute // 10
    """
    __tablename__ = "token_usage_hourly"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    username        = Column(String(50), nullable=False)
    usage_date      = Column(Date, nullable=False)
    hour            = Column(Integer, nullable=False)  # legacy (read-only)
    slot            = Column(Integer, nullable=True)   # 0-143
    model_id        = Column(String(100), nullable=False, default="unknown")
    input_tokens    = Column(BigInteger, default=0)
    output_tokens   = Column(BigInteger, default=0)
    cache_creation_input_tokens = Column(BigInteger, default=0)
    cache_read_input_tokens     = Column(BigInteger, default=0)
    total_tokens    = Column(BigInteger, default=0)
    cost_usd        = Column(Numeric(12, 6), default=0)
    cost_krw        = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("username", "usage_date", "slot", "model_id",
                         name="uq_slot_user_date_slot_model"),
        Index("ix_hourly_usage_date_slot", "usage_date", "slot"),
    )


class TokenUsageEvent(Base):
    """Producer publish event 단위 dedupe 테이블.

    Worker가 stream에서 이벤트를 consume할 때마다 INSERT를 시도하고,
    request_id 중복(ON CONFLICT DO NOTHING) 시 daily/hourly UPSERT를 skip.
    이로써 at-least-once delivery → exactly-once accounting을 달성.

    90일 후 자동 삭제(retention) — Bedrock 분기 빌링 cycle(3개월)과 일치.
    """
    __tablename__ = "token_usage_event"

    request_id   = Column(String(36), primary_key=True)  # uuid4 string
    username     = Column(String(50), nullable=False)
    model_id     = Column(String(100), nullable=False)
    recorded_at  = Column(DateTime(timezone=True), nullable=False)
    source       = Column(String(20))  # 'console-cli' | 'onlyoffice' | 'webchat' | ...
    input_tokens = Column(BigInteger, default=0)
    output_tokens = Column(BigInteger, default=0)
    cache_creation_input_tokens = Column(BigInteger, default=0)
    cache_read_input_tokens     = Column(BigInteger, default=0)
    cost_usd     = Column(Numeric(12, 6), default=0)
    created_at   = Column(DateTime(timezone=True),
                          default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_event_recorded_at", "recorded_at"),
    )
