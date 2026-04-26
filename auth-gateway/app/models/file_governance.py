"""파일 거버넌스 모델 — 사용자 파일의 분류·TTL·상태 관리.

GovernedFile: Pod 에이전트가 보고한 파일 정보 + 자동 분류 결과
FileClassification / FileStatus: str Enum — 코드 가독성 + 테스트 계약.
EncryptionState: DRM Phase 2 암호화 FSM 상태 (PLAIN → ENCRYPTING → ENCRYPTED / FAILED).
"""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text

from app.core.database import Base


class FileClassification(str, Enum):
    """파일 분류 — classification 컬럼 값 집합."""

    SENSITIVE = "sensitive"
    NORMAL = "normal"
    UNKNOWN = "unknown"


class FileStatus(str, Enum):
    """파일 상태 — status 컬럼 값 집합."""

    ACTIVE = "active"
    QUARANTINE = "quarantine"
    EXPIRED = "expired"
    DELETED = "deleted"


class EncryptionState(str, Enum):
    """암호화 FSM 상태.

    PLAIN       → 미암호화 (기본값, 백필 대상)
    ENCRYPTING  → 백필 작업자가 소유권 획득 후 진행 중 (10분 타임아웃)
    ENCRYPTED   → AES-256-GCM 암호화 완료, vault_id/encrypted_dek 유효
    FAILED      → 암호화 실패 (재시도 대상)
    """

    PLAIN = "plain"
    ENCRYPTING = "encrypting"
    ENCRYPTED = "encrypted"
    FAILED = "failed"


class GovernedFile(Base):
    """거버넌스 관리 파일 — Pod 에이전트가 스캔한 파일의 분류·TTL 정보."""

    __tablename__ = "governed_files"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 소유자·파일 식별
    username = Column(String(50), nullable=False, index=True)   # 사용자 사번
    filename = Column(String(255), nullable=False)               # 파일명 (basename)
    file_path = Column(String(500), nullable=False)              # 전체 경로 (고유 식별자)
    file_type = Column(String(20), nullable=True)                # 확장자 or MIME (미판별 시 NULL)
    file_size_bytes = Column(BigInteger, default=0)

    # 분류 결과
    classification = Column(String(20), default="unknown")       # "sensitive"|"normal"|"unknown"
    classification_reason = Column(Text, nullable=True)          # 분류 근거

    # 상태 관리
    status = Column(String(20), default="quarantine")            # "quarantine"|"active"|"expired"

    # TTL
    ttl_days = Column(Integer, nullable=True)                    # 보존 일수
    expires_at = Column(DateTime(timezone=True), nullable=True)  # 만료 일시

    # 타임스탬프
    classified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ----- DRM Phase 2: AES-256-GCM Envelope Encryption -----
    vault_id = Column(String(500), nullable=True)
    encrypted_dek = Column(Text, nullable=True)
    encryption_state = Column(
        String(20),
        nullable=False,
        default=EncryptionState.PLAIN.value,
        server_default="plain",
    )
    backfill_completed_at = Column(DateTime(timezone=True), nullable=True)
