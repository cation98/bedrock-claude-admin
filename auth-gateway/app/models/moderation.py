"""콘텐츠 검열 위반 기록 모델.

moderation_violations: AI 검열에서 차단된 발송 시도를 영구 기록.
관리자 감사 및 반복 위반자 제재에 활용.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.core.database import Base


class ModerationViolation(Base):
    """AI 콘텐츠 검열 위반 기록."""

    __tablename__ = "moderation_violations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, index=True)       # 위반자 사번
    action_type = Column(String(50), nullable=False)                # e.g. "app_share_mms"
    content = Column(Text, nullable=False)                          # 차단된 메시지 원문
    violation_category = Column(String(50), nullable=True)          # personal|commercial|profanity|violence
    violation_reason = Column(Text, nullable=True)                  # AI 판단 이유
    app_name = Column(String(100), nullable=True)                   # 연관 앱 이름
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
