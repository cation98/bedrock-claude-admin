from datetime import datetime

from pydantic import BaseModel


class SafetyReportResponse(BaseModel):
    id: int
    title: str
    description: str | None = None
    reporter_name: str | None = None
    location: str | None = None
    severity: str | None = None
    status: str | None = None
    is_resolved: bool = False
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class SafetyReportList(BaseModel):
    total: int
    items: list[SafetyReportResponse]
