"""토큰 할당 정책 — 템플릿 + 사용자별 할당."""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Numeric

from app.core.database import Base


class TokenQuotaTemplate(Base):
    """관리자가 생성하는 토큰 할당 정책 템플릿."""

    __tablename__ = "token_quota_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)  # e.g., "standard", "premium"
    description = Column(String(200), nullable=True)
    cost_limit_usd = Column(Numeric(10, 2), nullable=False)  # 주기별 USD 한도
    refresh_cycle = Column(String(20), nullable=False)  # daily, weekly, monthly
    is_unlimited = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class TokenQuotaAssignment(Base):
    """사용자에게 할당된 토큰 정책 (템플릿에서 복사)."""

    __tablename__ = "token_quota_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True)  # FK to users (논리적 참조)
    username = Column(String(50), nullable=False, index=True)
    template_name = Column(String(50), nullable=False)  # 할당 시점의 템플릿 이름
    cost_limit_usd = Column(Numeric(10, 2), nullable=False)  # 템플릿에서 복사
    refresh_cycle = Column(String(20), nullable=False)  # 템플릿에서 복사
    is_unlimited = Column(Boolean, default=False)
    assigned_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
