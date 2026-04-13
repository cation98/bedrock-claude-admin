"""파일 거버넌스 감사 로그 — 분류·만료 이벤트 기록."""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.core.database import Base


class FileAuditAction(str, Enum):
    """파일 감사 로그 액션 유형.

    FileAuditLog.action 컬럼에 저장되는 표준 값. 레거시 raw string
    ("vault_upload", "vault_download" 등)은 DB가 String(30)이므로 공존 가능 —
    신규 코드는 이 Enum을 사용하여 오탈자/일관성을 보장.
    """

    UPLOAD = "upload"
    CLASSIFY = "classify"
    DELETE = "delete"
    SHARE = "share"
    ACCESS = "access"
    QUARANTINE = "quarantine"
    EXPIRE = "expire"
    EXTEND = "extend"


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
