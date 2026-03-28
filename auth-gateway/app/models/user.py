from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Boolean, Integer, JSON

from app.core.database import Base


class User(Base):
    """플랫폼 사용자 (SSO 인증 시 자동 등록/업데이트)."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)  # 사번 (e.g. N1102359)
    name = Column(String(100))  # 표시 이름 (first_name)
    phone_number = Column(String(20))
    region_name = Column(String(50))  # 담당 (e.g. AT/DT추진담당)
    team_name = Column(String(50))    # 팀 (e.g. AT/DT개발팀)
    job_name = Column(String(50))     # 직급 (e.g. 팀장)
    role = Column(String(20), default="user")  # admin, user
    is_active = Column(Boolean, default=True)
    is_approved = Column(Boolean, default=False, nullable=False)  # 관리자 승인 여부
    pod_ttl = Column(String(10), default="4h", nullable=False)  # Pod 수명: unlimited, 30d, 7d, 1d, 8h, 4h
    approved_at = Column(DateTime(timezone=True), nullable=True)  # 승인 일시
    last_login_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    storage_retention = Column(String(10), default="30d", nullable=False)  # 7d, 30d, 90d, unlimited
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    security_policy = Column(JSON, nullable=True, default=None)


class SecurityTemplate(Base):
    """관리자가 생성하는 재사용 가능한 보안 정책 템플릿."""

    __tablename__ = "security_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)  # e.g., "문서Log분석"
    description = Column(String(200), nullable=True)
    policy = Column(JSON, nullable=False)  # Same structure as user.security_policy
    created_by = Column(String(50), nullable=True)  # admin username
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
