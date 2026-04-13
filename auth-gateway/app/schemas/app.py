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
    # 인증 모드: "system" = 플랫폼 webapp-login(SSO+2FA) | "custom" = 앱 자체 구현(2FA 필수)
    auth_mode: str = "system"
    custom_2fa_attested: bool = False      # auth_mode="custom" 선택 시 2FA 구현을 확인한다는 배포자 약속


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
    auth_mode: str = "system"
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None
    view_count: int = 0
    unique_viewers: int = 0
    dau: int = 0
    mau: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AppACLRequest(BaseModel):
    """앱 접근 권한 부여 요청."""

    grant_type: str = "user"   # user | team | region | job | company
    grant_value: str           # 사번, 팀명, 지역, 직책, 또는 "*"


class AppACLResponse(BaseModel):
    """앱 접근 권한 정보 응답."""

    id: int
    app_id: int
    grant_type: str
    grant_value: str
    granted_by: str
    granted_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = {"from_attributes": True}


class AppACLDetailResponse(BaseModel):
    """앱 접근 권한 상세 응답."""

    id: int
    app_id: int
    grant_type: str
    grant_value: str
    granted_by: str
    display_label: str | None = None  # "안전기술팀 (팀)" or "N1102359 (김부장)"
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


class AppStatsResponse(BaseModel):
    """앱 통계 응답."""

    app_id: int
    app_name: str
    dau: int = 0          # 오늘 순방문자
    mau: int = 0          # 이번 달 순방문자
    total_views: int = 0  # 총 조회수
    recent_visitors: list[dict] = []  # 최근 접속자 [{username, name, team, visited_at}]


class AppACLOptionsResponse(BaseModel):
    """ACL 드롭다운 옵션 응답."""

    teams: list[str] = []
    regions: list[str] = []
    jobs: list[str] = []
