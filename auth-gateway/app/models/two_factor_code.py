"""2FA 인증 코드 모델.

SMS/텔레그램으로 발송된 6자리 인증 코드의 생성, 만료, 시도 횟수를 추적.
보안 정책:
  - 코드 유효기간: 5분
  - 최대 시도 횟수: 5회
  - 계정 잠금: 15분 내 3회 이상 max attempts 도달 시
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Integer, Boolean, Index

from app.core.database import Base


class TwoFactorCode(Base):
    """2FA 인증 코드 레코드."""

    __tablename__ = "two_factor_codes"
    __table_args__ = (
        Index("ix_two_factor_codes_username_created", "username", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(50), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    phone_number = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    verified = Column(Boolean, default=False, nullable=False)
