"""웹앱 배포 및 ACL 관리 API 라우터.

Endpoints:
  POST   /api/v1/apps/deploy              -- 앱 배포
  DELETE /api/v1/apps/{app_name}           -- 앱 삭제
  POST   /api/v1/apps/{app_name}/rollback  -- 앱 롤백
  GET    /api/v1/apps/my                   -- 내 배포 앱 목록
  GET    /api/v1/apps/shared              -- 나에게 공유된 앱 목록
  GET    /api/v1/apps/{app_name}/acl       -- 앱 접근 허용 사용자 목록
  POST   /api/v1/apps/{app_name}/acl       -- 앱 접근 권한 부여
  DELETE /api/v1/apps/{app_name}/acl/{username} -- 앱 접근 권한 회수
  GET    /api/v1/apps/auth-check           -- Ingress auth-url (SSO + ACL 검증)
  GET    /api/v1/users/search              -- 승인된 사용자 검색

보안 고려사항:
  - auth-check는 NGINX Ingress의 auth-url로 호출되며, 모든 /apps/* 요청에 대해
    SSO 인증 + ACL 권한을 검증하는 보안 게이트웨이 역할을 수행.
  - 앱 소유자만 배포/삭제/롤백/ACL 관리 가능 (소유권 검증 필수).
  - ACL은 revoked_at으로 소프트 삭제하여 감사 추적 가능.
"""

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import decode_token, get_current_user, get_current_user_or_pod
from app.models.app import AppACL, DeployedApp
from app.models.user import User
from app.schemas.app import (
    AppACLDetailResponse,
    AppACLRequest,
    AppACLResponse,
    DeployedAppResponse,
    DeployRequest,
    RollbackRequest,
    UserSearchResponse,
    UserSearchResult,
)

router = APIRouter(prefix="/api/v1/apps", tags=["apps"])
logger = logging.getLogger(__name__)

# ---------- 앱 이름 유효성 검사 ----------

# 앱 이름: 영문 소문자, 숫자, 하이픈만 허용 (2-50자)
# K8s 리소스 이름 제약 + URL 경로 안전성을 위한 제한
APP_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{0,48}[a-z0-9]$")


def _validate_app_name(app_name: str) -> str:
    """앱 이름 유효성 검사 (K8s 리소스 이름 호환 + URL 안전).

    허용: 영문 소문자, 숫자, 하이픈 (2-50자)
    금지: 대문자, 언더스코어, 특수문자, 시작/끝 하이픈
    """
    if not APP_NAME_PATTERN.match(app_name):
        raise HTTPException(
            status_code=400,
            detail=(
                "앱 이름은 영문 소문자, 숫자, 하이픈만 사용 가능합니다 "
                "(2-50자, 시작/끝은 영문 소문자 또는 숫자)."
            ),
        )
    return app_name


# ---------- 소유권 검증 헬퍼 ----------


def _get_owned_app(app_name: str, username: str, db: Session) -> DeployedApp:
    """현재 사용자가 소유한 앱을 조회. 미소유 시 403, 미존재 시 404 반환."""
    app = (
        db.query(DeployedApp)
        .filter(
            DeployedApp.app_name == app_name,
            DeployedApp.status != "deleted",
        )
        .first()
    )
    if not app:
        raise HTTPException(status_code=404, detail="앱을 찾을 수 없습니다")
    if app.owner_username != username:
        raise HTTPException(status_code=403, detail="앱 소유자만 이 작업을 수행할 수 있습니다")
    return app


# ---------- 배포 권한 검증 헬퍼 ----------


def _require_deploy_permission(username: str, db: Session) -> User:
    """사용자의 앱 배포 권한(can_deploy_apps) 확인. 미승인 시 403 반환."""
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if not user.can_deploy_apps:
        raise HTTPException(
            status_code=403,
            detail="앱 배포 권한이 없습니다. 관리자에게 배포 권한을 요청하세요.",
        )
    return user


# ==================== 고정 경로 엔드포인트 (동적 경로보다 먼저 선언) ====================


@router.get("/auth-check", status_code=200)
async def auth_check(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Ingress auth-url 콜백: SSO 인증 + ACL 권한 검증.

    NGINX Ingress가 /apps/* 요청마다 이 엔드포인트를 호출한다.
    200 반환 시 요청 허용, 401/403 반환 시 요청 차단.

    검증 흐름:
      1. JWT 토큰 추출 (Authorization 헤더 또는 claude_token 쿠키)
      2. 토큰 유효성 검증 (만료, 서명)
      3. X-Original-URI에서 앱 소유자/이름 파싱
      4. 요청자가 앱 소유자이거나 ACL에 등록된 사용자인지 확인

    보안 참고:
      - X-Original-URI는 NGINX Ingress가 설정하는 헤더로, 클라이언트가 조작 불가.
      - ACL은 revoked_at이 NULL인 활성 레코드만 검사.
      - 관리자(admin)는 모든 앱에 접근 가능.
    """
    # 1. JWT 토큰 추출 (Authorization 헤더 → claude_token 쿠키 순서)
    user_payload = None

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        user_payload = decode_token(token, settings)

    if user_payload is None:
        token = request.cookies.get("claude_token", "")
        if token:
            user_payload = decode_token(token, settings)

    if user_payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다",
        )

    requesting_username = user_payload.get("sub", "")
    if not requesting_username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 토큰입니다",
        )

    # 2. X-Original-URI에서 앱 소유자/이름 파싱
    # 형식: /apps/{owner_username}/{app_name}/...
    original_uri = request.headers.get("X-Original-URI", "")
    if not original_uri:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Original-URI 헤더가 없습니다",
        )

    # URI 경로 파싱: /apps/{owner}/{app-name} 또는 /apps/{owner}/{app-name}/...
    uri_match = re.match(r"^/apps/([^/]+)/([^/?]+)", original_uri)
    if not uri_match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="잘못된 앱 경로 형식입니다",
        )

    owner_username = uri_match.group(1).upper()  # 사번은 대문자로 정규화
    app_name = uri_match.group(2)

    # 3. 관리자는 모든 앱에 접근 가능
    requesting_role = user_payload.get("role", "user")
    if requesting_role == "admin":
        return _auth_check_success(requesting_username)

    # 4. 앱 소유자는 자신의 앱에 접근 가능
    if requesting_username.upper() == owner_username:
        return _auth_check_success(requesting_username)

    # 5. ACL 검사: 요청자가 해당 앱의 활성 ACL에 등록되어 있는지 확인
    app = (
        db.query(DeployedApp)
        .filter(
            DeployedApp.owner_username == owner_username,
            DeployedApp.app_name == app_name,
            DeployedApp.status != "deleted",
        )
        .first()
    )
    if not app:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="앱을 찾을 수 없거나 접근 권한이 없습니다",
        )

    acl_entry = (
        db.query(AppACL)
        .filter(
            AppACL.app_id == app.id,
            AppACL.granted_username == requesting_username.upper(),
            AppACL.revoked_at.is_(None),  # 활성 ACL만
        )
        .first()
    )
    if not acl_entry:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이 앱에 대한 접근 권한이 없습니다",
        )

    return _auth_check_success(requesting_username)


def _auth_check_success(username: str) -> dict:
    """auth-check 성공 응답. X-Auth-Username 헤더를 포함한다.

    NGINX Ingress는 auth-url 응답의 헤더를 upstream으로 전달할 수 있다.
    auth_response_headers 설정으로 X-Auth-Username을 앱에 전달.
    """
    from fastapi.responses import JSONResponse

    return JSONResponse(
        content={"authenticated": True, "username": username},
        headers={"X-Auth-Username": username},
    )


@router.get("/my", response_model=list[DeployedAppResponse])
async def list_my_apps(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """내가 배포한 앱 목록 조회."""
    username = current_user["sub"]
    apps = (
        db.query(DeployedApp)
        .filter(
            DeployedApp.owner_username == username,
            DeployedApp.status != "deleted",
        )
        .order_by(DeployedApp.created_at.desc())
        .all()
    )
    return [DeployedAppResponse.model_validate(a) for a in apps]


@router.get("/shared", response_model=list[DeployedAppResponse])
async def list_shared_apps(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """나에게 공유된 앱 목록 조회 (ACL에 등록된 앱)."""
    username = current_user["sub"]

    # 활성 ACL이 있는 앱 ID 목록 조회
    acl_app_ids = (
        db.query(AppACL.app_id)
        .filter(
            AppACL.granted_username == username,
            AppACL.revoked_at.is_(None),
        )
        .subquery()
    )

    apps = (
        db.query(DeployedApp)
        .filter(
            DeployedApp.id.in_(acl_app_ids),
            DeployedApp.status != "deleted",
        )
        .order_by(DeployedApp.created_at.desc())
        .all()
    )
    return [DeployedAppResponse.model_validate(a) for a in apps]


# ==================== 배포/삭제/롤백 ====================


@router.post("/deploy", response_model=DeployedAppResponse, status_code=status.HTTP_201_CREATED)
async def deploy_app(
    request: DeployRequest,
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """앱 배포 (신규 배포 또는 재배포).

    배포 권한(can_deploy_apps)이 있는 사용자만 호출 가능.
    동일 이름의 앱이 이미 존재하면 재배포(업데이트) 처리.
    """
    username = current_user["sub"]

    # 배포 권한 확인
    _require_deploy_permission(username, db)

    # 앱 이름 유효성 검사
    _validate_app_name(request.app_name)

    # 동일 이름의 기존 앱 확인 (소유자 기준)
    existing_app = (
        db.query(DeployedApp)
        .filter(
            DeployedApp.owner_username == username,
            DeployedApp.app_name == request.app_name,
            DeployedApp.status != "deleted",
        )
        .first()
    )

    if existing_app:
        # 재배포: 기존 앱 업데이트
        existing_app.version = request.version
        existing_app.status = "running"
        existing_app.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing_app)
        logger.info(f"App redeployed: {username}/{request.app_name} v={request.version}")
        return DeployedAppResponse.model_validate(existing_app)

    # 신규 배포
    app_url = f"/apps/{username}/{request.app_name}/"
    pod_name = f"app-{username.lower()}-{request.app_name}"

    new_app = DeployedApp(
        owner_username=username,
        app_name=request.app_name,
        app_url=app_url,
        pod_name=pod_name,
        status="running",
        version=request.version,
    )
    db.add(new_app)
    db.commit()
    db.refresh(new_app)

    # ACL 초기 사용자 추가 (배포 시 지정한 사용자들)
    if request.acl_usernames:
        for acl_username in request.acl_usernames:
            acl_user = acl_username.upper()
            # 자기 자신은 ACL에 추가하지 않음 (소유자는 항상 접근 가능)
            if acl_user == username:
                continue
            # 승인된 사용자인지 확인
            target_user = (
                db.query(User)
                .filter(User.username == acl_user, User.is_approved == True)  # noqa: E712
                .first()
            )
            if target_user:
                acl = AppACL(
                    app_id=new_app.id,
                    granted_username=acl_user,
                    granted_by=username,
                )
                db.add(acl)
        db.commit()

    logger.info(f"App deployed: {username}/{request.app_name} v={request.version}")
    return DeployedAppResponse.model_validate(new_app)


@router.delete("/{app_name}", status_code=200)
async def undeploy_app(
    app_name: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """앱 삭제 (소유자만 가능). 상태를 deleted로 변경하여 소프트 삭제."""
    username = current_user["sub"]
    app = _get_owned_app(app_name, username, db)

    app.status = "deleted"
    app.updated_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(f"App undeployed: {username}/{app_name}")
    return {"deleted": True, "app_name": app_name}


@router.post("/{app_name}/rollback", response_model=DeployedAppResponse)
async def rollback_app(
    app_name: str,
    request: RollbackRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """앱 롤백 (소유자만 가능). 지정된 버전으로 되돌린다."""
    username = current_user["sub"]
    app = _get_owned_app(app_name, username, db)

    app.version = request.version
    app.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(app)

    logger.info(f"App rolled back: {username}/{app_name} → v={request.version}")
    return DeployedAppResponse.model_validate(app)


# ==================== ACL 관리 ====================


@router.get("/{app_name}/acl", response_model=list[AppACLDetailResponse])
async def list_app_acl(
    app_name: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """앱 접근 허용 사용자 목록 (소유자만 조회 가능)."""
    username = current_user["sub"]
    app = _get_owned_app(app_name, username, db)

    # 활성 ACL 목록 + 사용자 이름/팀 조인
    acl_entries = (
        db.query(AppACL)
        .filter(
            AppACL.app_id == app.id,
            AppACL.revoked_at.is_(None),
        )
        .order_by(AppACL.granted_at.desc())
        .all()
    )

    # 사용자 정보 일괄 조회 (N+1 방지)
    usernames = [entry.granted_username for entry in acl_entries]
    users_map = {}
    if usernames:
        users = (
            db.query(User)
            .filter(User.username.in_(usernames))
            .all()
        )
        users_map = {u.username: u for u in users}

    results = []
    for entry in acl_entries:
        user = users_map.get(entry.granted_username)
        results.append(AppACLDetailResponse(
            id=entry.id,
            app_id=entry.app_id,
            granted_username=entry.granted_username,
            granted_by=entry.granted_by,
            user_name=user.name if user else None,
            team_name=user.team_name if user else None,
            granted_at=entry.granted_at,
            revoked_at=entry.revoked_at,
        ))

    return results


@router.post("/{app_name}/acl", response_model=AppACLResponse, status_code=status.HTTP_201_CREATED)
async def grant_app_access(
    app_name: str,
    request: AppACLRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """앱 접근 권한 부여 (소유자만 가능).

    중복 방지: 이미 활성 ACL이 있으면 409 반환.
    이전에 회수된 ACL이 있으면 새로운 레코드로 재부여.
    """
    username = current_user["sub"]
    app = _get_owned_app(app_name, username, db)

    target_username = request.username.upper()

    # 자기 자신에게 권한 부여 방지
    if target_username == username:
        raise HTTPException(
            status_code=400,
            detail="앱 소유자는 이미 접근 권한이 있습니다",
        )

    # 대상 사용자 존재 + 승인 여부 확인
    target_user = (
        db.query(User)
        .filter(User.username == target_username, User.is_approved == True)  # noqa: E712
        .first()
    )
    if not target_user:
        raise HTTPException(
            status_code=404,
            detail="승인된 사용자를 찾을 수 없습니다",
        )

    # 활성 ACL 중복 확인
    existing_acl = (
        db.query(AppACL)
        .filter(
            AppACL.app_id == app.id,
            AppACL.granted_username == target_username,
            AppACL.revoked_at.is_(None),
        )
        .first()
    )
    if existing_acl:
        raise HTTPException(
            status_code=409,
            detail="이미 접근 권한이 부여된 사용자입니다",
        )

    # ACL 생성
    acl = AppACL(
        app_id=app.id,
        granted_username=target_username,
        granted_by=username,
    )
    db.add(acl)
    db.commit()
    db.refresh(acl)

    logger.info(f"ACL granted: {target_username} → {username}/{app_name}")
    return AppACLResponse.model_validate(acl)


@router.delete("/{app_name}/acl/{target_username}", status_code=200)
async def revoke_app_access(
    app_name: str,
    target_username: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """앱 접근 권한 회수 (소유자만 가능). revoked_at 설정으로 소프트 삭제."""
    username = current_user["sub"]
    app = _get_owned_app(app_name, username, db)

    target_upper = target_username.upper()
    acl = (
        db.query(AppACL)
        .filter(
            AppACL.app_id == app.id,
            AppACL.granted_username == target_upper,
            AppACL.revoked_at.is_(None),
        )
        .first()
    )
    if not acl:
        raise HTTPException(
            status_code=404,
            detail="해당 사용자의 활성 접근 권한을 찾을 수 없습니다",
        )

    acl.revoked_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(f"ACL revoked: {target_upper} from {username}/{app_name}")
    return {"revoked": True, "username": target_upper, "app_name": app_name}


# ==================== 사용자 검색 (ACL 관리 UI용) ====================


@router.get(
    "/user-search",
    response_model=UserSearchResponse,
    summary="승인된 사용자 검색 (ACL 관리용)",
)
async def search_users(
    q: str,
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """승인된 사용자를 사번 또는 이름으로 검색.

    ACL 관리 UI에서 사용자를 검색하여 접근 권한을 부여할 때 사용.
    최대 20건까지 반환.
    """
    if not q or len(q.strip()) < 1:
        raise HTTPException(status_code=400, detail="검색어를 입력해주세요")

    # SQL Injection 방지: SQLAlchemy ORM 사용 (파라미터 바인딩)
    search_term = f"%{q.strip()}%"
    users = (
        db.query(User)
        .filter(
            User.is_approved == True,  # noqa: E712
            (User.username.ilike(search_term) | User.name.ilike(search_term)),
        )
        .order_by(User.username)
        .limit(20)
        .all()
    )

    return UserSearchResponse(
        total=len(users),
        results=[
            UserSearchResult(
                username=u.username,
                name=u.name,
                team_name=u.team_name,
            )
            for u in users
        ],
    )
