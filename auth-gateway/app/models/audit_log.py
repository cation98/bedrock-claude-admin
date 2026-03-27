"""감사 로그 — 모든 보안 관련 이벤트 기록."""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, JSON

from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    actor = Column(String(50), nullable=False)       # 행위자 사번
    action = Column(String(50), nullable=False)       # 행위 유형
    target = Column(String(100), nullable=True)       # 대상
    detail = Column(Text, nullable=True)              # 상세 내용
    ip_address = Column(String(45), nullable=True)    # 요청 IP
    metadata_ = Column("metadata", JSON, nullable=True)


class AuditAction:
    """감사 로그 행위 유형 상수."""
    LOGIN_SSO = "login_sso"
    LOGIN_2FA_SENT = "login_2fa_sent"
    LOGIN_2FA_OK = "login_2fa_ok"
    LOGIN_2FA_FAIL = "login_2fa_fail"
    LOGIN_LOCKED = "login_locked"
    LOGIN_BYPASS = "login_bypass"
    POD_CREATE = "pod_create"
    POD_TERMINATE = "pod_terminate"
    POD_MOVE = "pod_move"
    POD_ASSIGN = "pod_assign"
    NODE_SCALE_UP = "node_scale_up"
    NODE_SCALE_DOWN = "node_scale_down"
    NODE_DRAIN = "node_drain"
    SECURITY_UPDATE = "security_update"
    SECURITY_TEMPLATE = "security_template"
    USER_APPROVE = "user_approve"
    USER_REVOKE = "user_revoke"
    USER_ADD = "user_add_direct"
    ADMIN_LOGIN = "admin_login"
