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
  POST   /api/v1/files/datasets/{name}/verify-access      -- 공유 파일 SMS 인증 시작
  POST   /api/v1/files/datasets/{name}/verify-access-code -- SMS 코드 검증 → 임시 토큰 발급

보안 고려사항:
  - 데이터셋 소유자만 공유 설정/해제 가능 (소유권 검증 필수).
  - ACL은 revoked_at으로 소프트 삭제하여 감사 추적 가능.
  - share_type은 "user" 또는 "team"만 허용.
  - 공유 받은 사용자가 민감 파일 접근 시 SMS 인증 필요 (30분 임시 토큰 발급).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import create_access_token, decode_token, get_current_user, get_current_user_or_pod
from app.models.file_governance import GovernedFile
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


@router.post("/datasets/{name}/verify-access")
async def verify_share_access(
    name: str,
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """공유 민감 파일 접근을 위한 SMS 인증 시작.

    플로우:
    1. 사용자가 공유 받은 파일 접근 요청
    2. 파일이 sensitive로 분류되어 있으면 SMS 인증 필요
    3. 인증 코드 발송 → code_id 반환
    4. 클라이언트가 /verify-access-code로 코드 검증
    5. 검증 성공 → 임시 접근 토큰 발급 (30분 유효)
    """
    username = current_user["sub"]

    # 1. 데이터셋 조회
    dataset = db.query(SharedDataset).filter(SharedDataset.dataset_name == name).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다")

    # 소유자는 SMS 인증 불필요
    if dataset.owner_username == username:
        return {"access_granted": True, "message": "소유자는 인증이 필요 없습니다"}

    # 2. ACL 확인 (공유 받은 사용자인지)
    user = db.query(User).filter(User.username == username).first()
    team_name = user.team_name if user else None

    acl_filter = (
        FileShareACL.dataset_id == dataset.id,
        FileShareACL.revoked_at.is_(None),
    )

    if team_name:
        has_access = db.query(FileShareACL).filter(
            FileShareACL.dataset_id == dataset.id,
            FileShareACL.revoked_at.is_(None),
            (
                ((FileShareACL.share_type == "user") & (FileShareACL.share_target == username))
                | ((FileShareACL.share_type == "team") & (FileShareACL.share_target == team_name))
            ),
        ).first()
    else:
        has_access = db.query(FileShareACL).filter(
            FileShareACL.dataset_id == dataset.id,
            FileShareACL.revoked_at.is_(None),
            FileShareACL.share_type == "user",
            FileShareACL.share_target == username,
        ).first()

    if not has_access:
        raise HTTPException(status_code=403, detail="이 데이터셋에 대한 접근 권한이 없습니다")

    # 3. 민감 파일인지 확인 (GovernedFile 조회)
    governed = db.query(GovernedFile).filter(
        GovernedFile.username == dataset.owner_username,
        GovernedFile.filename == dataset.dataset_name,
        GovernedFile.classification == "sensitive",
    ).first()

    if not governed:
        # 민감하지 않으면 바로 접근 허용
        return {"access_granted": True, "message": "일반 파일은 인증이 필요 없습니다"}

    # 4. SMS 인증 코드 발송
    if not user or not user.phone_number:
        raise HTTPException(status_code=400, detail="전화번호가 등록되어 있지 않습니다")

    from app.services.two_factor_service import check_lockout, generate_code

    check_lockout(username, db)
    code_id, code = generate_code(username, user.phone_number, db)

    logger.info(
        f"Share access SMS sent: user={username}, dataset={name}, code_id={code_id}"
    )

    return {
        "access_granted": False,
        "requires_sms": True,
        "code_id": code_id,
        "message": f"{user.phone_number[-4:]}로 인증코드를 발송했습니다",
    }


@router.post("/datasets/{name}/verify-access-code")
async def verify_access_code(
    name: str,
    request_body: dict[str, Any],  # {"code_id": str, "code": str}
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """SMS 인증코드 검증 → 임시 접근 토큰 발급 (30분 유효)."""
    from app.services.two_factor_service import verify_code

    code_id = request_body.get("code_id")
    code = request_body.get("code")

    if not code_id or not code:
        raise HTTPException(status_code=400, detail="code_id와 code가 필요합니다")

    verify_code(code_id, code, db)  # raises on failure

    username = current_user["sub"]
    access_token = create_access_token(
        data={"sub": username, "dataset": name, "type": "share_access"},
        settings=settings,
        expires_delta=timedelta(minutes=30),
    )

    logger.info(f"Share access token issued: user={username}, dataset={name}")

    return {
        "access_granted": True,
        "access_token": access_token,
        "expires_in": 1800,
        "message": "인증 완료. 30분간 파일에 접근할 수 있습니다.",
    }


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


# ── /files/ Ingress auth-url: 본인 Pod + admin만 접근 허용 ──

@router.get("/files-auth-check", include_in_schema=False)
async def files_auth_check(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Ingress auth-url: /files/{pod_name}/ 요청의 인증·인가 검증.

    - admin 역할: 모든 Pod의 /files/ 접근 허용
    - 일반 사용자: 본인 Pod의 /files/ 접근만 허용 (Hub 파일 탐색기 API용)
    - 미인증: 401 → 로그인 페이지로 리다이렉트
    """
    # JWT 추출 (Authorization 헤더 → claude_token 쿠키)
    user_payload = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        user_payload = decode_token(auth_header.split(" ", 1)[1], settings)
    if user_payload is None:
        token = request.cookies.get("claude_token", "")
        if token:
            user_payload = decode_token(token, settings)
    if user_payload is None:
        raise HTTPException(status_code=401, detail="인증이 필요합니다")

    requesting_username = user_payload.get("sub", "")
    if not requesting_username:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰")

    # 역할 확인
    user = db.query(User).filter(User.username == requesting_username).first()
    is_admin = user and user.role == "admin"

    # nginx-ingress는 X-Original-URL (full URL)을 보냄, X-Original-URI는 미제공
    # X-Original-URL: https://claude.skons.net/hub/claude-terminal-n1102359/...
    # X-Original-URI: /hub/claude-terminal-n1102359/... (fallback)
    original_url = request.headers.get("X-Original-URL", "")
    original_uri = request.headers.get("X-Original-URI", "")
    if original_url:
        from urllib.parse import urlparse
        original_uri = urlparse(original_url).path
    parts = original_uri.strip("/").split("/")
    pod_name = parts[1] if len(parts) >= 2 else ""

    # pod_name에서 username 추출: claude-terminal-n1102359 → N1102359
    pod_owner = pod_name.replace("claude-terminal-", "").upper() if pod_name.startswith("claude-terminal-") else ""

    if is_admin:
        return {"status": "ok", "user": requesting_username, "access": "admin"}

    if pod_owner == requesting_username.upper():
        return {"status": "ok", "user": requesting_username, "access": "owner"}

    raise HTTPException(status_code=403, detail="접근 권한이 없습니다")
