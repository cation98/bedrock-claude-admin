"""공유 스킬 DB 모델."""

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean

from app.core.database import Base


class SharedSkill(Base):
    """사용자가 제출한 공유 스킬/프롬프트.

    사용자 Pod에서 만든 스킬, CLAUDE.md 설정, 프롬프트 템플릿 등을
    중앙에 제출하여 관리자 승인 후 전체 사용자에게 배포.
    """

    __tablename__ = "shared_skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    author_username = Column(String(50), nullable=False, index=True)
    author_name = Column(String(100))
    title = Column(String(200), nullable=False)
    description = Column(String(500))
    category = Column(String(50), default="skill")  # skill, claude-md, prompt, snippet
    content = Column(Text, nullable=False)
    is_approved = Column(Boolean, default=False, index=True)
    approved_by = Column(String(50), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    usage_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
