"""토큰 사용량 일별/시간별 누적."""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, BigInteger, String, Date, DateTime, Numeric, UniqueConstraint

from app.core.database import Base


class TokenUsageDaily(Base):
    __tablename__ = "token_usage_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False)
    user_name = Column(String(100))
    usage_date = Column(Date, nullable=False)
    input_tokens = Column(BigInteger, default=0)
    output_tokens = Column(BigInteger, default=0)
    total_tokens = Column(BigInteger, default=0)
    cost_usd = Column(Numeric(10, 4), default=0)
    cost_krw = Column(Integer, default=0)
    session_minutes = Column(Integer, default=0)
    last_activity_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint('username', 'usage_date'),)


class TokenUsageHourly(Base):
    """5분 단위 토큰 사용량 — 스파크라인 차트용.

    slot: 0-287 (24h × 12 slots/h = 288 slots per day)
    slot 계산: hour * 12 + minute // 5
    """
    __tablename__ = "token_usage_hourly"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False)
    usage_date = Column(Date, nullable=False)
    hour = Column(Integer, nullable=False)  # legacy: kept for backward compat
    slot = Column(Integer, nullable=True)   # 0-287 (5-min resolution)
    input_tokens = Column(BigInteger, default=0)
    output_tokens = Column(BigInteger, default=0)
    total_tokens = Column(BigInteger, default=0)
    cost_usd = Column(Numeric(10, 4), default=0)
    cost_krw = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("username", "usage_date", "slot", name="uq_slot_user_date_slot"),
    )
