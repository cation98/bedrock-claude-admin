"""SQLCipher 암호화 키 관리 서비스.

키 라이프사이클:
  1. 생성: secure-put으로 SQLite 파일 등록 시 랜덤 256-bit 키 생성
  2. 저장: 플랫폼 DB에 해시 저장 (또는 AWS Secrets Manager)
  3. 주입: Pod 시작 시 환경변수로 주입
  4. 만료: TTL 도래 시 키 삭제 → SQLite 파일 접근 불가 → 파일 삭제
"""

import secrets
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import Session

from app.core.database import Base

logger = logging.getLogger(__name__)


class SQLCipherKey(Base):
    """SQLCipher 암호화 키 메타데이터."""
    __tablename__ = "sqlcipher_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, index=True)
    db_name = Column(String(255), nullable=False)  # e.g., "hr_records.db"
    key_hash = Column(String(64), nullable=False)  # SHA-256 of the actual key
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked = Column(Integer, default=0)  # 0=active, 1=revoked


def generate_key(username: str, db_name: str, ttl_days: int, db: Session) -> str:
    """새 SQLCipher 키를 생성하고 메타데이터를 저장.

    Args:
        username: 사용자 사번 (Pod 소유자)
        db_name: SQLite 파일명 (e.g. "hr_records.db")
        ttl_days: 키 유효 기간 (일). 0이면 만료 없음.
        db: SQLAlchemy 세션

    Returns:
        평문 256-bit hex 키 (Pod 환경변수 주입용). DB에는 SHA-256 해시만 저장.
    """
    key = secrets.token_hex(32)  # 256-bit key
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days) if ttl_days else None

    record = SQLCipherKey(
        username=username,
        db_name=db_name,
        key_hash=key_hash,
        expires_at=expires_at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    logger.info(f"SQLCipher key generated for {username}/{db_name}, expires={expires_at}")
    return key  # 평문 키 반환 (Pod에 주입용)


def get_key_hash(username: str, db_name: str, db: Session) -> str | None:
    """활성 키의 SHA-256 해시를 조회.

    만료된 키는 자동으로 revoked=1로 표시하고 None 반환.

    Args:
        username: 사용자 사번
        db_name: SQLite 파일명
        db: SQLAlchemy 세션

    Returns:
        64자 hex SHA-256 해시, 또는 활성 키가 없으면 None.
    """
    record = (
        db.query(SQLCipherKey)
        .filter(
            SQLCipherKey.username == username,
            SQLCipherKey.db_name == db_name,
            SQLCipherKey.revoked == 0,
        )
        .order_by(SQLCipherKey.created_at.desc())
        .first()
    )
    if not record:
        return None
    # Check expiry — SQLite may return naive datetimes; normalise to UTC-aware for comparison.
    if record.expires_at:
        expires = record.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            record.revoked = 1
            db.commit()
            return None
    return record.key_hash


def revoke_key(username: str, db_name: str, db: Session) -> bool:
    """활성 키를 모두 폐기 (만료 강제 또는 수동 취소).

    Args:
        username: 사용자 사번
        db_name: SQLite 파일명
        db: SQLAlchemy 세션

    Returns:
        True if at least one key was revoked, False if no active keys found.
    """
    records = (
        db.query(SQLCipherKey)
        .filter(
            SQLCipherKey.username == username,
            SQLCipherKey.db_name == db_name,
            SQLCipherKey.revoked == 0,
        )
        .all()
    )
    for r in records:
        r.revoked = 1
    db.commit()
    logger.info(f"SQLCipher keys revoked for {username}/{db_name}: {len(records)} record(s)")
    return len(records) > 0
