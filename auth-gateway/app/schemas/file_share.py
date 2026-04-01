"""팀 파일 공유 스키마 — 요청/응답 모델."""

from datetime import datetime

from pydantic import BaseModel


# ---------- 데이터셋 ----------


class DatasetCreateRequest(BaseModel):
    """데이터셋 등록 요청 (파일 업로드 후 호출)."""

    dataset_name: str
    file_path: str
    file_type: str = "sqlite"
    description: str | None = None
    file_size_bytes: int = 0


class DatasetResponse(BaseModel):
    """데이터셋 정보 응답."""

    id: int
    owner_username: str
    dataset_name: str
    file_path: str
    file_type: str
    file_size_bytes: int
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class DatasetWithACLCountResponse(DatasetResponse):
    """데이터셋 정보 + ACL 수 응답 (내 데이터셋 목록용)."""

    acl_count: int = 0


class SharedDatasetResponse(BaseModel):
    """나에게 공유된 데이터셋 응답 (소유자 정보 포함)."""

    id: int
    owner_username: str
    owner_name: str | None = None
    dataset_name: str
    file_path: str
    file_type: str
    file_size_bytes: int
    description: str | None = None
    share_type: str          # "user" or "team"
    share_target: str        # 공유 대상 (사번 또는 팀명)
    created_at: datetime | None = None


# ---------- ACL ----------


class ShareRequest(BaseModel):
    """공유 설정 요청."""

    share_type: str   # "user" or "team"
    target: str       # 사번(N1001063) 또는 팀명(강북Access담당)


class ShareACLResponse(BaseModel):
    """공유 ACL 정보 응답."""

    id: int
    dataset_id: int
    share_type: str
    share_target: str
    granted_by: str
    granted_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = {"from_attributes": True}


# ---------- Pod 마운트 ----------


class SharedMountResponse(BaseModel):
    """Pod 생성 시 마운트할 공유 데이터셋 정보."""

    owner_username: str
    dataset_name: str
    file_path: str


# ---------- 팀 목록 ----------


class TeamListResponse(BaseModel):
    """조직 목록 응답."""

    teams: list[str]
