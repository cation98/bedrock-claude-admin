"""가이드 콘텐츠 모델."""

from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Integer, Text, Boolean, Index

from app.core.database import Base


class Guide(Base):
    """명령어·활용 가이드 콘텐츠."""

    __tablename__ = "guides"
    __table_args__ = (
        Index("ix_guides_category", "category"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)           # markdown content
    category = Column(String(50), default="general")  # general, command, workflow, tip
    author_username = Column(String(50), nullable=False)
    is_published = Column(Boolean, default=False)     # 관리자 승인 후 공개
    view_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
