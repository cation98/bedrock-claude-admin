"""팀 파일 공유 모델 — 개인/조직 단위 공유 ACL.

shared_datasets: 사용자가 공유한 파일/디렉토리 단위 (EFS 경로 기반)
file_share_acl: 데이터셋별 접근 허용 대상 목록 (user 또는 team 단위)
"""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from app.core.database import Base


class SharedDataset(Base):
    """공유 데이터셋 — 사용자가 공유한 파일/디렉토리 단위."""

    __tablename__ = "shared_datasets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_username = Column(String(50), nullable=False, index=True)  # 소유자 사번
    dataset_name = Column(String(100), nullable=False)               # e.g., "erp-2026q1"
    file_path = Column(String(255), nullable=False)                  # EFS 상대 경로: shared-data/erp.sqlite
    file_type = Column(String(20), default="sqlite")                 # sqlite, csv, parquet
    file_size_bytes = Column(BigInteger, default=0)
    description = Column(String(500))

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("owner_username", "dataset_name", name="uq_owner_dataset"),
    )


class FileShareACL(Base):
    """파일 공유 ACL — 개인(user) 또는 조직(team) 단위.

    revoked_at이 NULL이면 활성 상태 (AppACL 패턴과 동일).
    """

    __tablename__ = "file_share_acl"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("shared_datasets.id"), nullable=False, index=True)
    share_type = Column(String(20), nullable=False)    # "user" or "team"
    share_target = Column(String(100), nullable=False)  # 사번(user) 또는 팀명(team)
    granted_by = Column(String(50), nullable=False)     # 권한 부여자 사번

    granted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    revoked_at = Column(DateTime(timezone=True), nullable=True)  # NULL = 활성, 값 있으면 회수됨
