"""웹앱 배포 및 ACL 관리 스키마."""

from datetime import datetime

from pydantic import BaseModel


class DeployRequest(BaseModel):
    """앱 배포 요청."""

    app_name: str                          # 배포할 앱 이름
    version: str | None = None             # 배포 버전 (선택, 미지정 시 auto-generated)
    visibility: str = "private"            # 접근 범위: "private" | "company"
    app_port: int = 3000                   # Pod 내부 포트 (기본 3000)
    acl_usernames: list[str] | None = None # 접근 허용 사번 목록 (선택)


class RollbackRequest(BaseModel):
    """앱 롤백 요청."""

    version: str  # 롤백할 대상 버전


class DeployedAppResponse(BaseModel):
    """배포된 앱 정보 응답."""

    id: int
    owner_username: str
    app_name: str
    app_url: str | None = None
    pod_name: str | None = None
    status: str
    version: str | None = None
    visibility: str = "private"
    app_port: int = 3000
    view_count: int = 0
    unique_viewers: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AppACLRequest(BaseModel):
    """앱 접근 권한 부여 요청."""

    username: str  # 접근 허용할 사번


class AppACLResponse(BaseModel):
    """앱 접근 권한 정보 응답."""

    id: int
    app_id: int
    granted_username: str
    granted_by: str
    granted_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = {"from_attributes": True}


class AppACLDetailResponse(BaseModel):
    """앱 접근 권한 상세 응답 (사용자 이름 포함)."""

    id: int
    app_id: int
    granted_username: str
    granted_by: str
    user_name: str | None = None     # 허용된 사용자 표시 이름
    team_name: str | None = None     # 허용된 사용자 팀
    granted_at: datetime | None = None
    revoked_at: datetime | None = None


class UserSearchResult(BaseModel):
    """사용자 검색 결과 항목."""

    username: str
    name: str | None = None
    team_name: str | None = None


class UserSearchResponse(BaseModel):
    """사용자 검색 결과 응답."""

    total: int
    results: list[UserSearchResult]
