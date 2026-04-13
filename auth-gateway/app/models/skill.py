"""공유 스킬 DB 모델.

SharedSkill: 스킬 스토어에 공유된 스킬 (관리자 승인 + 스토어 퍼블리시 겸용)
SkillInstall: 스킬 설치 기록 (사용자별)
SkillGovernanceEvent: 승인/반려/삭제 이력 감사 (Phase 2 A+B)
"""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Column, ForeignKey, Index, Integer, String, DateTime, Text, Boolean
from sqlalchemy.sql import func

from app.core.database import Base


class SkillApprovalStatus(str, Enum):
    """shared_skills.approval_status 값 집합."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class SkillGovernanceEventType(str, Enum):
    """skill_governance_events.event_type 값 집합."""

    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"
    DELETE = "delete"
    VERSION_BUMP = "version_bump"


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

    # Phase 2 A: 명시적 상태 + 반려 필드 + 버전
    # is_approved Boolean은 하위 호환 유지 — approval_status가 SSOT.
    approval_status = Column(
        String(20), default=SkillApprovalStatus.PENDING.value, nullable=False, index=True
    )
    version = Column(Integer, default=1, nullable=False)
    rejected_by = Column(String(50), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)

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


class SkillGovernanceEvent(Base):
    """스킬 승인/반려/삭제 이력 감사 (Phase 2 B).

    approve / reject / delete / version_bump 이벤트를 이벤트 소싱 방식으로 기록.
    reject 사유나 delete 시 최종 상태 보존 등 감사 추적성 확보.
    """

    __tablename__ = "skill_governance_events"
    __table_args__ = (
        Index("ix_sge_skill_created", "skill_id", "created_at"),
        Index("ix_sge_actor", "actor_username"),
        Index("ix_sge_type", "event_type"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # skill_id는 FK이되 ON DELETE SET NULL로 skill 삭제 후에도 이력 유지 가능하게
    skill_id = Column(
        Integer,
        ForeignKey("shared_skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type = Column(String(20), nullable=False)  # SkillGovernanceEventType 값
    actor_username = Column(String(50), nullable=False)
    actor_role = Column(String(20), nullable=False, default="admin")  # user|admin
    detail = Column(Text, nullable=True)  # reject 사유, 기타 컨텍스트
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
