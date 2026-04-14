"""서비스 점검 모드 모델.

점검 모드 활성화 시 claude.skons.net에 점검 페이지만 표시.
관리자 경로(/admin)는 예외 처리.
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from app.core.database import Base


class MaintenanceMode(Base):
    """서비스 점검 모드 설정 (단일 레코드, id=1 고정)."""

    __tablename__ = "maintenance_mode"

    id          = Column(Integer, primary_key=True, default=1)
    is_active   = Column(Boolean, default=False, nullable=False)
    title       = Column(String(200), default="서비스 점검 중", nullable=False)
    description = Column(Text, default="", nullable=False)
    start_time  = Column(DateTime(timezone=True), nullable=True)   # 점검 시작
    end_time    = Column(DateTime(timezone=True), nullable=True)   # 완료 예정
    updated_by  = Column(String(50), nullable=True)                # 마지막 수정 관리자
    updated_at  = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
