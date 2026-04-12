from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SessionCreateRequest(BaseModel):
    session_type: str = "workshop"  # workshop | daily


class SessionResponse(BaseModel):
    id: int
    username: str
    user_name: str | None = None  # 성명 (first_name)
    pod_name: str
    pod_status: str
    session_type: str
    terminal_url: str | None = None
    files_url: str | None = None
    hub_url: str | None = None
    started_at: datetime | None = None
    terminated_at: datetime | None = None
    expires_at: datetime | None = None  # Pod TTL 기반 만료 시간 (unlimited이면 None)
    last_active_at: datetime | None = None
    idle_minutes: int | None = None  # 마지막 활동 이후 경과 분 (running 상태일 때만)

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    total: int
    sessions: list[SessionResponse]


class BulkSessionRequest(BaseModel):
    """관리자용: 다수 사용자 일괄 세션 생성."""
    usernames: list[str]
    session_type: str = "workshop"


class UiSourceRequest(BaseModel):
    """UI 소스 사용 이벤트 기록 요청 — T23."""
    source: Literal["webchat", "console"]
