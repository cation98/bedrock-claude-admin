"""공지사항 모델."""

from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Integer, Text, Boolean

from app.core.database import Base


class Announcement(Base):
    """관리자 공지사항 (Hub 배너 + 모달 상세)."""

    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)       # 배너에 표시할 한줄 제목
    content = Column(Text, nullable=False)             # 모달에 표시할 상세 내용
    author_username = Column(String(50), nullable=False)  # 작성자 사번
    is_active = Column(Boolean, default=True)          # 활성 공지 여부
    is_pinned = Column(Boolean, default=False)         # 상단 고정 여부
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=True)  # 자동 만료 시각 (NULL=무기한)
