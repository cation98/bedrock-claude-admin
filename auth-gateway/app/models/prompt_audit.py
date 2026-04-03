"""프롬프트 감사 모델 — 사용자 대화 분류 + 보안 감사."""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Date, DateTime, Text, Boolean, JSON, BigInteger,
    UniqueConstraint,
)

from app.core.database import Base


class PromptAuditSummary(Base):
    """일별 사용자 프롬프트 분류 요약."""
    __tablename__ = "prompt_audit_summary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, index=True)
    audit_date = Column(Date, nullable=False)
    total_prompts = Column(Integer, default=0)
    total_chars = Column(BigInteger, default=0)
    # 카테고리별 건수 (JSON): {"data_analysis": 5, "coding": 10, ...}
    category_counts = Column(JSON, default=dict)
    flagged_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("username", "audit_date", name="uq_audit_user_date"),
    )


class PromptAuditConversation(Base):
    """세션별 대화 이력 — user + assistant 전체 저장."""
    __tablename__ = "prompt_audit_conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, index=True)
    session_id = Column(String(100), nullable=False, index=True)
    message_type = Column(String(20), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime(timezone=True))
    collected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("username", "session_id", "message_type", "timestamp",
                         name="uq_conversation_msg"),
    )


class PromptAuditFlag(Base):
    """보안 위반 플래그 — 개별 의심 프롬프트 기록."""
    __tablename__ = "prompt_audit_flags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, index=True)
    flagged_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    category = Column(String(50), nullable=False)       # 보안 위반 유형
    severity = Column(String(20), default="medium")      # low, medium, high, critical
    prompt_excerpt = Column(Text)                        # 플래그된 프롬프트 앞 200자
    reason = Column(String(200))                         # 플래그 사유
    reviewed = Column(Boolean, default=False)
    reviewed_by = Column(String(50))
    reviewed_at = Column(DateTime(timezone=True))
