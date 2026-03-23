"""사용자 관리 스키마 (관리자 승인/TTL 관리용)."""

from datetime import datetime

from pydantic import BaseModel, field_validator


# Pod TTL 허용 값
VALID_POD_TTLS = {"unlimited", "30d", "7d", "1d", "8h", "4h"}

# Pod TTL → 초 변환 맵
POD_TTL_SECONDS_MAP = {
    "unlimited": 0,
    "30d": 2592000,
    "7d": 604800,
    "1d": 86400,
    "8h": 28800,
    "4h": 14400,
}


class UserResponse(BaseModel):
    """사용자 정보 응답."""

    id: int
    username: str
    name: str | None
    role: str
    is_approved: bool
    pod_ttl: str
    approved_at: datetime | None
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    """사용자 목록 응답."""

    total: int
    users: list[UserResponse]


class ApproveRequest(BaseModel):
    """사용자 승인 요청 (Pod TTL 지정 가능)."""

    pod_ttl: str = "4h"

    @field_validator("pod_ttl")
    @classmethod
    def validate_pod_ttl(cls, v: str) -> str:
        if v not in VALID_POD_TTLS:
            raise ValueError(f"pod_ttl must be one of: {', '.join(sorted(VALID_POD_TTLS))}")
        return v


class TTLUpdateRequest(BaseModel):
    """Pod TTL 변경 요청."""

    pod_ttl: str  # unlimited, 30d, 7d, 1d, 8h, 4h

    @field_validator("pod_ttl")
    @classmethod
    def validate_pod_ttl(cls, v: str) -> str:
        if v not in VALID_POD_TTLS:
            raise ValueError(f"pod_ttl must be one of: {', '.join(sorted(VALID_POD_TTLS))}")
        return v
