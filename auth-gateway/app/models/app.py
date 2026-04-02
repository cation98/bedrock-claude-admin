"""배포된 웹앱 및 접근 제어(ACL) 모델.

deployed_apps: 사용자가 배포한 앱의 메타데이터 (Pod 이름, 상태, 버전 등)
app_acl: 앱별 접근 허용 사용자 목록 (granted/revoked 관리)
"""

from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, Index

from app.core.database import Base


class DeployedApp(Base):
    """배포된 웹앱 (1 앱 = 1 K8s Pod in claude-apps namespace)."""

    __tablename__ = "deployed_apps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_username = Column(String(50), nullable=False, index=True)  # 배포자 사번
    app_name = Column(String(100), nullable=False)                   # 앱 이름
    app_url = Column(String(255))                                    # /apps/{username}/{app-name}/
    pod_name = Column(String(100), unique=True)                      # app-{username}-{app-name}
    status = Column(String(20), default="running")                   # running, stopped, inactive, deleted
    version = Column(String(50))                                     # git tag 또는 auto-generated
    visibility = Column(String(20), default="private")               # private | company
    app_port = Column(Integer, default=3000)                         # Pod 내부 포트 (3000, 5000, 8501 등)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AppView(Base):
    """앱 조회 기록 (auth-check 프록시 미들웨어에서 비동기 INSERT)."""

    __tablename__ = "app_views"
    __table_args__ = (
        Index("ix_app_views_app_id_viewed_at", "app_id", "viewed_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    app_id = Column(Integer, ForeignKey("deployed_apps.id"), nullable=False)
    viewer_user_id = Column(String(50), nullable=False)  # 접속자 사번
    viewed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AppACL(Base):
    """앱별 접근 제어 목록 (revoked_at이 NULL이면 활성 상태)."""

    __tablename__ = "app_acl"

    id = Column(Integer, primary_key=True, autoincrement=True)
    app_id = Column(Integer, ForeignKey("deployed_apps.id"), nullable=False, index=True)
    granted_username = Column(String(50), nullable=False)  # 접근 허용 사번
    granted_by = Column(String(50), nullable=False)        # 권한 부여자 사번
    granted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    revoked_at = Column(DateTime(timezone=True), nullable=True)  # NULL = 활성, 값 있으면 회수됨
