from datetime import datetime

from pydantic import BaseModel


class SessionCreateRequest(BaseModel):
    session_type: str = "workshop"  # workshop | daily


class SessionResponse(BaseModel):
    id: int
    username: str
    pod_name: str
    pod_status: str
    session_type: str
    terminal_url: str | None = None
    started_at: datetime | None = None
    terminated_at: datetime | None = None

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    total: int
    sessions: list[SessionResponse]


class BulkSessionRequest(BaseModel):
    """관리자용: 다수 사용자 일괄 세션 생성."""
    usernames: list[str]
    session_type: str = "workshop"
