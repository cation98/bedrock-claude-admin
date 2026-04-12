"""UI 소스 이벤트 — webchat / console 사용률 추적."""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String

from app.core.database import Base


class UiSourceEvent(Base):
    """사용자가 Hub에서 어떤 UI 소스(webchat|console)를 사용했는지 기록.

    Hub 포탈이 webchat ↔ console 전환 시 POST /api/v1/sessions/ui-source 를 호출하며
    이 테이블에 이벤트가 적재된다.
    Admin Dashboard의 /analytics/ui-split 페이지에서 주간/월간 집계에 활용.
    """

    __tablename__ = "ui_source_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False)
    source = Column(String(20), nullable=False)  # "webchat" | "console"
    recorded_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_ui_source_events_username_recorded", "username", "recorded_at"),
    )
