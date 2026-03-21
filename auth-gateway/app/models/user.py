from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Boolean, Integer

from app.core.database import Base


class User(Base):
    """플랫폼 사용자 (SSO 인증 시 자동 등록/업데이트)."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)  # 사번 (e.g. N1102359)
    name = Column(String(100))
    phone_number = Column(String(20))
    role = Column(String(20), default="user")  # admin, user
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
