"""외부 API 프록시 관련 모델.

AllowedDomain: 허용된 외부 도메인 화이트리스트 (와일드카드 지원)
ProxyAccessLog: 프록시 접근 로그 (허용/차단 기록)
"""

from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Integer, Boolean, Text, Index

from app.core.database import Base


class AllowedDomain(Base):
    """프록시를 통해 접근 가능한 외부 도메인 화이트리스트.

    와일드카드 도메인 예시:
      - 'apis.data.go.kr' → 정확한 도메인만 허용
      - '*.amazonaws.com' → bedrock.us-east-1.amazonaws.com 등 서브도메인 허용
    """

    __tablename__ = "allowed_domains"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(255), nullable=False, unique=True)   # e.g. 'apis.data.go.kr' or '*.amazonaws.com'
    is_wildcard = Column(Boolean, default=False)                 # True이면 서브도메인 매칭
    description = Column(Text, nullable=True)                    # 도메인 용도 설명
    enabled = Column(Boolean, default=True)                      # 비활성화 시 차단
    created_by = Column(String(50), nullable=True)               # 등록한 관리자 사번
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ProxyAccessLog(Base):
    """프록시 접근 로그 — 허용/차단 기록을 저장하여 감사 추적 지원."""

    __tablename__ = "proxy_access_logs"
    __table_args__ = (
        Index("ix_proxy_access_logs_created_at", "created_at"),
        Index("ix_proxy_access_logs_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), nullable=True)                  # 요청자 사번
    domain = Column(String(255), nullable=True)                  # 접근 시도한 도메인
    method = Column(String(10), nullable=True)                   # CONNECT (HTTPS) 등
    allowed = Column(Boolean, nullable=True)                     # True=허용, False=차단
    response_time_ms = Column(Integer, nullable=True)            # 프록시 응답 시간(ms)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
