"""공유 스킬 DB 모델.

SharedSkill: 스킬 스토어에 공유된 스킬 (관리자 승인 + 스토어 퍼블리시 겸용)
SkillInstall: 스킬 설치 기록 (사용자별)
"""

from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, Index, Integer, String, DateTime, Text, Boolean
from sqlalchemy.sql import func

from app.core.database import Base


class SharedSkill(Base):
    """사용자가 제출한 공유 스킬/프롬프트.

    사용자 Pod에서 만든 스킬, CLAUDE.md 설정, 프롬프트 템플릿 등을
    중앙에 제출하여 관리자 승인 후 전체 사용자에게 배포.
    스킬 스토어에서 퍼블리시/검색/설치도 지원.
    """

    __tablename__ = "shared_skills"
    __table_args__ = (
        Index("ix_shared_skills_owner", "owner_username"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 기존 컬럼 (관리자 승인 기반 스킬 공유)
    author_username = Column(String(50), nullable=True, index=True)
    author_name = Column(String(100))
    title = Column(String(200), nullable=True)
    description = Column(Text)
    category = Column(String(50), default="skill")  # skill, claude-md, prompt, snippet, slash_command, workflow
    content = Column(Text, nullable=True)
    is_approved = Column(Boolean, default=False, index=True)
    approved_by = Column(String(50), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    usage_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # 스킬 스토어 컬럼 (퍼블리시/검색/설치용)
    owner_username = Column(String(50), nullable=True)
    skill_name = Column(String(100), nullable=True)  # slash command name e.g. /db-report
    display_name = Column(String(200), nullable=True)
    skill_type = Column(String(20), default="slash_command")  # slash_command | workflow
    skill_dir_name = Column(String(100), nullable=True)  # directory name in .claude/skills/
    install_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SkillInstall(Base):
    """스킬 설치 기록."""

    __tablename__ = "skill_installs"
    __table_args__ = (
        Index("ix_skill_installs_user", "username"),
        Index("ix_skill_installs_skill", "skill_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(Integer, ForeignKey("shared_skills.id"), nullable=False)
    username = Column(String(50), nullable=False)
    installed_at = Column(DateTime(timezone=True), server_default=func.now())
    uninstalled_at = Column(DateTime(timezone=True))
