"""파일 거버넌스 감사 로그 — 분류·만료 이벤트 기록."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.core.database import Base


class FileAuditLog(Base):
    """파일 거버넌스 감사 로그 — 분류·만료·삭제 이벤트."""

    __tablename__ = "file_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    username = Column(String(50), nullable=False, index=True)   # 파일 소유자
    action = Column(String(30), nullable=False)                  # "classify"|"expire"|"delete"
    filename = Column(String(255), nullable=True)
    file_path = Column(String(500), nullable=True)
    detail = Column(Text, nullable=True)                         # 상세 메시지
    ip_address = Column(String(45), nullable=True)               # 요청 IP (API 호출 시)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
