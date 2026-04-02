"""팀 파일 공유 API 라우터.

Endpoints:
  POST   /api/v1/files/datasets              -- 데이터셋 등록
  GET    /api/v1/files/datasets/my            -- 내가 소유한 데이터셋 목록
  GET    /api/v1/files/datasets/shared        -- 나에게 공유된 데이터셋 목록
  POST   /api/v1/files/datasets/{name}/share  -- 공유 설정
  DELETE /api/v1/files/datasets/{name}/share/{acl_id} -- 공유 해제
  GET    /api/v1/files/datasets/{name}/share  -- 공유 대상 목록
  GET    /api/v1/files/shared-mounts/{username} -- Pod 생성 시 마운트 목록
  GET    /api/v1/files/teams                  -- 조직 목록 (공유 대상 선택용)

보안 고려사항:
  - 데이터셋 소유자만 공유 설정/해제 가능 (소유권 검증 필수).
  - ACL은 revoked_at으로 소프트 삭제하여 감사 추적 가능.
  - share_type은 "user" 또는 "team"만 허용.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, get_current_user_or_pod
from app.models.file_share import FileShareACL, SharedDataset
from app.models.user import User
from app.schemas.file_share import (
    DatasetCreateRequest,
    DatasetResponse,
    DatasetWithACLCountResponse,
    ShareACLResponse,
    SharedDatasetResponse,
    SharedMountResponse,
    ShareRequest,
    TeamListResponse,
)

router = APIRouter(prefix="/api/v1/files", tags=["file-share"])
logger = logging.getLogger(__name__)

VALID_SHARE_TYPES = ("user", "team")


# ---------- 소유권 검증 헬퍼 ----------


def _get_owned_dataset(dataset_name: str, username: str, db: Session) -> SharedDataset:
    """현재 사용자가 소유한 데이터셋을 조회. 미소유 시 403, 미존재 시 404 반환."""
    dataset = (
        db.query(SharedDataset)
        .filter(
            SharedDataset.dataset_name == dataset_name,
            SharedDataset.owner_username == username,
        )
        .first()
    )
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다")
    return dataset


# ==================== 고정 경로 엔드포인트 (동적 경로보다 먼저 선언) ====================


@router.get("/datasets/my", response_model=list[DatasetWithACLCountResponse])
async def list_my_datasets(
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """내가 소유한 데이터셋 목록 (데이터셋별 활성 ACL 수 포함)."""
    username = current_user["sub"]

    # 데이터셋 + 활성 ACL 수를 서브쿼리로 조회 (N+1 방지)
    acl_count_sub = (
        db.query(
            FileShareACL.dataset_id,
            func.count(FileShareACL.id).label("acl_count"),
        )
        .filter(FileShareACL.revoked_at.is_(None))
        .group_by(FileShareACL.dataset_id)
        .subquery()
    )

    rows = (
        db.query(SharedDataset, func.coalesce(acl_count_sub.c.acl_count, 0).label("acl_count"))
        .outerjoin(acl_count_sub, SharedDataset.id == acl_count_sub.c.dataset_id)
        .filter(SharedDataset.owner_username == username)
        .order_by(SharedDataset.created_at.desc())
        .all()
    )

    return [
        DatasetWithACLCountResponse(
            id=ds.id,
            owner_username=ds.owner_username,
            dataset_name=ds.dataset_name,
            file_path=ds.file_path,
            file_type=ds.file_type,
            file_size_bytes=ds.file_size_bytes,
            description=ds.description,
            created_at=ds.created_at,
            updated_at=ds.updated_at,
            acl_count=acl_count,
        )
        for ds, acl_count in rows
    ]


@router.get("/datasets/shared", response_model=list[SharedDatasetResponse])
async def list_shared_datasets(
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """나에게 공유된 데이터셋 목록 (user 또는 team 단위)."""
    username = current_user["sub"]

    # 사용자의 team_name 조회
    user = db.query(User).filter(User.username == username).first()
    team_name = user.team_name if user else None

    # 활성 ACL 중 나에게 해당하는 항목 조회
    # (share_type=user AND share_target=username) OR (share_type=team AND share_target=team_name)
    acl_filter = FileShareACL.revoked_at.is_(None)
    if team_name:
        acl_filter = acl_filter & (
            (
                (FileShareACL.share_type == "user")
                & (FileShareACL.share_target == username)
            )
            | (
                (FileShareACL.share_type == "team")
                & (FileShareACL.share_target == team_name)
            )
        )
    else:
        acl_filter = acl_filter & (
            (FileShareACL.share_type == "user")
            & (FileShareACL.share_target == username)
        )

    rows = (
        db.query(SharedDataset, FileShareACL)
        .join(FileShareACL, SharedDataset.id == FileShareACL.dataset_id)
        .filter(acl_filter)
        .order_by(SharedDataset.created_at.desc())
        .all()
    )

    # 소유자 정보 일괄 조회 (N+1 방지)
    owner_usernames = list({ds.owner_username for ds, _ in rows})
    owners_map: dict[str, User] = {}
    if owner_usernames:
        owners = db.query(User).filter(User.username.in_(owner_usernames)).all()
        owners_map = {u.username: u for u in owners}

    return [
        SharedDatasetResponse(
            id=ds.id,
            owner_username=ds.owner_username,
            owner_name=owners_map.get(ds.owner_username, None) and owners_map[ds.owner_username].name,
            dataset_name=ds.dataset_name,
            file_path=ds.file_path,
            file_type=ds.file_type,
            file_size_bytes=ds.file_size_bytes,
            description=ds.description,
            share_type=acl.share_type,
            share_target=acl.share_target,
            created_at=ds.created_at,
        )
        for ds, acl in rows
    ]


@router.get("/shared-mounts/{username}", response_model=list[SharedMountResponse])
async def get_shared_mounts(
    username: str,
    db: Session = Depends(get_db),
):
    """Pod 생성 시 호출 — 해당 사용자에게 공유된 데이터셋의 마운트 경로 목록.

    K8s 서비스가 Pod 생성 시 이 엔드포인트를 호출하여
    readOnly 볼륨 마운트 목록을 결정한다.
    """
    shares = _get_shared_mounts_for_user(db, username)
    return [SharedMountResponse(**s) for s in shares]


@router.get("/teams", response_model=TeamListResponse)
async def list_teams(
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """조직 목록 — 공유 대상 선택 UI용.

    승인된 사용자의 team_name에서 고유 값만 추출.
    """
    rows = (
        db.query(User.team_name)
        .filter(
            User.is_approved == True,  # noqa: E712
            User.team_name.isnot(None),
            User.team_name != "",
        )
        .distinct()
        .order_by(User.team_name)
        .all()
    )
    return TeamListResponse(teams=[r[0] for r in rows])


@router.get("/regions")
async def list_regions(
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """담당(region) 목록 — 공유 대상 선택 UI용."""
    rows = (
        db.query(User.region_name)
        .filter(User.is_approved == True, User.region_name.isnot(None), User.region_name != "")
        .distinct()
        .order_by(User.region_name)
        .all()
    )
    return {"regions": [r[0] for r in rows]}


@router.get("/org-members")
async def list_org_members(
    region: str = None,
    team: str = None,
    job: str = None,
    q: str = None,
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """조직 구성원 검색 — 담당/팀/직책 필터 + 이름/사번 검색.

    사용 예:
      /org-members?region=경남Access담당
      /org-members?team=HR팀&job=팀장
      /org-members?q=김광우
      /org-members?job=실장
    """
    query = db.query(User).filter(User.is_approved == True)

    if region:
        query = query.filter(User.region_name == region)
    if team:
        query = query.filter(User.team_name == team)
    if job:
        query = query.filter(User.job_name == job)
    if q:
        query = query.filter(
            (User.username.ilike(f"%{q}%")) | (User.name.ilike(f"%{q}%"))
        )

    users = query.order_by(User.name).limit(30).all()
    return {
        "members": [
            {
                "username": u.username,
                "name": u.name,
                "region_name": u.region_name,
                "team_name": u.team_name,
                "job_name": u.job_name,
            }
            for u in users
        ]
    }


# ==================== 데이터셋 CRUD ====================


@router.post("/datasets", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    request: DatasetCreateRequest,
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """데이터셋 등록 (파일 업로드 완료 후 호출).

    동일 owner + dataset_name 중복 시 409 반환.
    """
    username = current_user["sub"]

    # 중복 확인
    existing = (
        db.query(SharedDataset)
        .filter(
            SharedDataset.owner_username == username,
            SharedDataset.dataset_name == request.dataset_name,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="동일한 이름의 데이터셋이 이미 존재합니다",
        )

    dataset = SharedDataset(
        owner_username=username,
        dataset_name=request.dataset_name,
        file_path=request.file_path,
        file_type=request.file_type,
        file_size_bytes=request.file_size_bytes,
        description=request.description,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    logger.info(f"Dataset created: {username}/{request.dataset_name}")
    return DatasetResponse.model_validate(dataset)


# ==================== 공유 ACL 관리 ====================


@router.get("/datasets/{name}/share")
async def list_dataset_shares(
    name: str,
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """공유 대상 목록 (데이터셋 소유자만 조회 가능)."""
    username = current_user["sub"]
    dataset = _get_owned_dataset(name, username, db)

    acl_entries = (
        db.query(FileShareACL)
        .filter(
            FileShareACL.dataset_id == dataset.id,
            FileShareACL.revoked_at.is_(None),
        )
        .order_by(FileShareACL.granted_at.desc())
        .all()
    )

    # 개인 공유의 경우 이름을 조회하여 포함
    result = []
    for e in acl_entries:
        data = ShareACLResponse.model_validate(e).model_dump()
        if e.share_type == "user":
            user = db.query(User).filter(User.username == e.share_target).first()
            data["target_name"] = user.name if user else None
        result.append(data)
    return result


@router.post(
    "/datasets/{name}/share",
    response_model=ShareACLResponse,
    status_code=status.HTTP_201_CREATED,
)
async def share_dataset(
    name: str,
    request: ShareRequest,
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """공유 설정 (데이터셋 소유자만 가능).

    share_type: "user" (개인) 또는 "team" (조직)
    target: 사번(user) 또는 팀명(team)
    중복 활성 ACL이 있으면 409 반환.
    """
    username = current_user["sub"]
    dataset = _get_owned_dataset(name, username, db)

    # share_type 유효성 검사
    if request.share_type not in VALID_SHARE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"share_type은 {VALID_SHARE_TYPES} 중 하나여야 합니다",
        )

    # 대상 유효성 검사
    if request.share_type == "user":
        # 대상 사용자 존재 + 승인 여부 확인
        target_user = (
            db.query(User)
            .filter(
                User.username == request.target.upper(),
                User.is_approved == True,  # noqa: E712
            )
            .first()
        )
        if not target_user:
            raise HTTPException(status_code=404, detail="승인된 사용자를 찾을 수 없습니다")
        # 자기 자신에게 공유 방지
        if request.target.upper() == username:
            raise HTTPException(status_code=400, detail="자신에게는 공유할 수 없습니다")
    elif request.share_type == "team":
        # 팀명이 실제로 존재하는지 확인
        team_exists = (
            db.query(User)
            .filter(
                User.team_name == request.target,
                User.is_approved == True,  # noqa: E712
            )
            .first()
        )
        if not team_exists:
            raise HTTPException(status_code=404, detail="해당 팀을 찾을 수 없습니다")

    share_target = request.target.upper() if request.share_type == "user" else request.target

    # 활성 ACL 중복 확인
    existing_acl = (
        db.query(FileShareACL)
        .filter(
            FileShareACL.dataset_id == dataset.id,
            FileShareACL.share_type == request.share_type,
            FileShareACL.share_target == share_target,
            FileShareACL.revoked_at.is_(None),
        )
        .first()
    )
    if existing_acl:
        raise HTTPException(
            status_code=409,
            detail="이미 공유가 설정된 대상입니다",
        )

    acl = FileShareACL(
        dataset_id=dataset.id,
        share_type=request.share_type,
        share_target=share_target,
        granted_by=username,
    )
    db.add(acl)
    db.commit()
    db.refresh(acl)

    logger.info(f"Share granted: {username}/{name} → {request.share_type}:{share_target}")
    return ShareACLResponse.model_validate(acl)


@router.delete("/datasets/{name}/share/{acl_id}", status_code=200)
async def revoke_share(
    name: str,
    acl_id: int,
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """공유 해제 (데이터셋 소유자만 가능). revoked_at 설정으로 소프트 삭제."""
    username = current_user["sub"]
    dataset = _get_owned_dataset(name, username, db)

    acl = (
        db.query(FileShareACL)
        .filter(
            FileShareACL.id == acl_id,
            FileShareACL.dataset_id == dataset.id,
            FileShareACL.revoked_at.is_(None),
        )
        .first()
    )
    if not acl:
        raise HTTPException(
            status_code=404,
            detail="해당 공유 설정을 찾을 수 없습니다",
        )

    acl.revoked_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(f"Share revoked: {username}/{name} ACL#{acl_id}")
    return {"revoked": True, "dataset_name": name, "acl_id": acl_id}


# ==================== 내부 헬퍼 (K8s 서비스에서도 사용) ====================


def _get_shared_mounts_for_user(db: Session, username: str) -> list[dict]:
    """특정 사용자에게 공유된 데이터셋 목록 (Pod 볼륨 마운트용).

    1. 사용자의 team_name 조회
    2. FileShareACL에서 (share_type=user AND share_target=username) OR
       (share_type=team AND share_target=team_name) 조건으로 활성 ACL 조회
    3. SharedDataset과 조인하여 마운트에 필요한 정보 반환
    """
    user = db.query(User).filter(User.username == username).first()
    team_name = user.team_name if user else None

    # ACL 필터 조건
    acl_filter = FileShareACL.revoked_at.is_(None)
    if team_name:
        acl_filter = acl_filter & (
            (
                (FileShareACL.share_type == "user")
                & (FileShareACL.share_target == username)
            )
            | (
                (FileShareACL.share_type == "team")
                & (FileShareACL.share_target == team_name)
            )
        )
    else:
        acl_filter = acl_filter & (
            (FileShareACL.share_type == "user")
            & (FileShareACL.share_target == username)
        )

    rows = (
        db.query(SharedDataset)
        .join(FileShareACL, SharedDataset.id == FileShareACL.dataset_id)
        .filter(acl_filter)
        .all()
    )

    # 중복 제거 (동일 데이터셋이 user+team 양쪽으로 공유된 경우)
    seen = set()
    results = []
    for ds in rows:
        key = (ds.owner_username, ds.dataset_name)
        if key not in seen:
            seen.add(key)
            results.append({
                "owner_username": ds.owner_username,
                "dataset_name": ds.dataset_name,
                "file_path": ds.file_path,
            })

    return results
