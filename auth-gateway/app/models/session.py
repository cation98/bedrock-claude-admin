from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Integer, ForeignKey

from app.core.database import Base


class TerminalSession(Base):
    """사용자 터미널 세션 (1 세션 = 1 K8s Pod)."""

    __tablename__ = "terminal_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    username = Column(String(50), nullable=False)

    # K8s Pod 정보
    pod_name = Column(String(100), unique=True)
    pod_status = Column(String(20), default="creating")  # creating, running, terminated, failed

    # 세션 메타
    session_type = Column(String(20), default="workshop")  # workshop, daily
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    terminated_at = Column(DateTime(timezone=True))
    last_active_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
