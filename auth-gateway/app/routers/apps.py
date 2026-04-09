"""웹앱 배포 및 ACL 관리 API 라우터.

Endpoints:
  POST   /api/v1/apps/deploy              -- 앱 배포
  DELETE /api/v1/apps/{app_name}           -- 앱 삭제
  POST   /api/v1/apps/{app_name}/rollback  -- 앱 롤백
  GET    /api/v1/apps/my                   -- 내 배포 앱 목록
  GET    /api/v1/apps/shared              -- 나에게 공유된 앱 목록
  GET    /api/v1/apps/{app_name}/acl       -- 앱 접근 허용 사용자 목록
  POST   /api/v1/apps/{app_name}/acl       -- 앱 접근 권한 부여
  DELETE /api/v1/apps/{app_name}/acl/{acl_id}   -- 앱 접근 권한 회수 (ACL ID 기반)
  GET    /api/v1/apps/auth-check           -- Ingress auth-url (SSO + ACL 검증)
  GET    /api/v1/users/search              -- 승인된 사용자 검색

보안 고려사항:
  - auth-check는 NGINX Ingress의 auth-url로 호출되며, 모든 /apps/* 요청에 대해
    SSO 인증 + ACL 권한을 검증하는 보안 게이트웨이 역할을 수행.
  - 앱 소유자만 배포/삭제/롤백/ACL 관리 가능 (소유권 검증 필수).
  - ACL은 revoked_at으로 소프트 삭제하여 감사 추적 가능.
"""

import asyncio
import logging
import re
from datetime import datetime, date, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal, get_db
from app.core.security import decode_token, get_current_user, get_current_user_or_pod
from app.models.app import AppACL, AppLike, AppView, DeployedApp
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

# auth-check 시 뷰 기록에서 제외할 정적 자산 확장자
_STATIC_ASSET_EXTENSIONS = frozenset({
    ".css", ".js", ".png", ".jpg", ".jpeg", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".map",
})

# 배포 허용 포트 범위 + 위험 포트 블랙리스트 (SSRF 방지)
_ALLOWED_PORT_MIN = 3000
_ALLOWED_PORT_MAX = 9999
_BLOCKED_PORTS = frozenset({
    6379,  # Redis
    5432,  # PostgreSQL
    3306,  # MySQL
    9200,  # Elasticsearch
    8443,  # K8s API
    8472,  # VXLAN (Flannel)
    6443,  # K8s API server
    2379,  # etcd
    2380,  # etcd peer
})

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
        # 401 반환 — NGINX Ingress auth-signin annotation이 /webapp-login으로 리다이렉트 처리
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

    # 2. X-Original-URI 또는 X-Original-URL에서 앱 slug/이름 파싱
    # nginx-ingress는 X-Original-URL(전체 URL) 또는 X-Original-URI(경로만) 전송
    original_uri = request.headers.get("X-Original-URI", "")
    if not original_uri:
        original_url = request.headers.get("X-Original-URL", "")
        if original_url:
            from urllib.parse import urlparse
            original_uri = urlparse(original_url).path
    if not original_uri:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Original-URI/URL 헤더가 없습니다",
        )

    # URI 경로 파싱: /apps/{slug}/{app-name} 또는 /apps/{slug}/{app-name}/...
    uri_match = re.match(r"^/apps/([^/]+)/([^/?]+)", original_uri)
    if not uri_match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="잘못된 앱 경로 형식입니다",
        )

    slug = uri_match.group(1)  # slug (8자 hex, 사번 비노출)
    app_name = uri_match.group(2)

    # slug로 앱 소유자 조회
    owner = db.query(User).filter(User.app_slug == slug).first()
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="앱을 찾을 수 없습니다",
        )
    owner_username = owner.username

    # 3. 관리자는 모든 앱에 접근 가능
    requesting_role = user_payload.get("role", "user")
    if requesting_role == "admin":
        await _maybe_record_view(original_uri, None, requesting_username)
        return _auth_check_success(requesting_username)

    # 4. 앱 소유자는 자신의 앱에 접근 가능
    if requesting_username.upper() == owner_username.upper():
        await _maybe_record_view(original_uri, None, requesting_username)
        return _auth_check_success(requesting_username)

    # 5. 앱 조회 (visibility + ACL 검사에 사용)
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

    # 5-2. ACL 검사 (5-type grant)
    # 요청자의 프로필 조회
    requesting_user = db.query(User).filter(User.username == requesting_username.upper()).first()

    active_acls = (
        db.query(AppACL)
        .filter(
            AppACL.app_id == app.id,
            AppACL.revoked_at.is_(None),
        )
        .all()
    )

    acl_matched = False
    for acl in active_acls:
        if acl.grant_type == "company":
            acl_matched = True
            break
        if acl.grant_type == "region" and requesting_user and requesting_user.region_name == acl.grant_value:
            acl_matched = True
            break
        if acl.grant_type == "team" and requesting_user and requesting_user.team_name == acl.grant_value:
            acl_matched = True
            break
        if acl.grant_type == "job" and requesting_user and requesting_user.job_name == acl.grant_value:
            acl_matched = True
            break
        if acl.grant_type == "user" and requesting_username.upper() == acl.grant_value.upper():
            acl_matched = True
            break

    if not acl_matched:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이 앱에 대한 접근 권한이 없습니다",
        )

    await _maybe_record_view(original_uri, app.id, requesting_username)
    return _auth_check_success(requesting_username)


def _auth_check_success(username: str) -> dict:
    """auth-check 성공 응답. X-Auth-Username 헤더를 포함한다.

    NGINX Ingress는 auth-url 응답의 헤더를 upstream으로 전달할 수 있다.
    auth_response_headers 설정으로 X-Auth-Username을 앱에 전달.
    """
    return JSONResponse(
        content={"authenticated": True, "username": username},
        headers={"X-Auth-Username": username},
    )


def _is_static_asset(uri: str) -> bool:
    """URI가 정적 자산(CSS, JS, 이미지, 폰트 등)인지 확인."""
    # 쿼리스트링 제거 후 확장자 검사
    path = uri.split("?", 1)[0]
    dot_idx = path.rfind(".")
    if dot_idx == -1:
        return False
    ext = path[dot_idx:].lower()
    return ext in _STATIC_ASSET_EXTENSIONS


async def _maybe_record_view(
    original_uri: str,
    app_id: int | None,
    viewer_username: str,
) -> None:
    """정적 자산이 아닌 경우 비동기로 뷰를 기록한다.

    app_id가 None인 경우(관리자/소유자 접근 등 앱 조회 전 단계)는 기록하지 않는다.
    auth-check 응답 속도에 영향을 주지 않도록 asyncio.create_task로 실행.
    async 함수이므로 running event loop에서 안전하게 create_task 호출 가능.
    """
    if app_id is None:
        return
    if _is_static_asset(original_uri):
        return
    try:
        asyncio.create_task(_record_view(app_id, viewer_username))
    except Exception:
        logger.debug("Failed to schedule view recording", exc_info=True)


async def _record_view(app_id: int, viewer_username: str) -> None:
    """app_views 테이블에 조회 기록 INSERT (비동기, 실패해도 무시).

    자체 DB 세션을 생성하여 요청 세션과 독립적으로 동작.
    """
    try:
        db = SessionLocal()
        try:
            view = AppView(
                app_id=app_id,
                viewer_user_id=viewer_username,
            )
            db.add(view)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.debug("Failed to record app view: app_id=%s, user=%s", app_id, viewer_username, exc_info=True)


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
    """나에게 공유된 앱 목록 조회 (5-type ACL 기반)."""
    username = current_user["sub"]

    # 요청자의 프로필 조회 (team, region, job 매칭용)
    requesting_user = db.query(User).filter(User.username == username.upper()).first()

    # 활성 ACL 중 요청자에게 매칭되는 조건 구성
    acl_conditions = [
        AppACL.grant_type == "company",  # 전사 공개
        (AppACL.grant_type == "user") & (AppACL.grant_value == username.upper()),
    ]
    if requesting_user:
        if requesting_user.region_name:
            acl_conditions.append(
                (AppACL.grant_type == "region") & (AppACL.grant_value == requesting_user.region_name)
            )
        if requesting_user.team_name:
            acl_conditions.append(
                (AppACL.grant_type == "team") & (AppACL.grant_value == requesting_user.team_name)
            )
        if requesting_user.job_name:
            acl_conditions.append(
                (AppACL.grant_type == "job") & (AppACL.grant_value == requesting_user.job_name)
            )

    acl_app_ids = (
        db.query(AppACL.app_id)
        .filter(
            AppACL.revoked_at.is_(None),
            or_(*acl_conditions),
        )
        .subquery()
    )

    apps = (
        db.query(DeployedApp)
        .filter(
            DeployedApp.id.in_(acl_app_ids),
            DeployedApp.owner_username != username,  # 본인 앱 제외 (my에서 조회)
            DeployedApp.status != "deleted",
        )
        .order_by(DeployedApp.created_at.desc())
        .all()
    )
    return [DeployedAppResponse.model_validate(a) for a in apps]


@router.get("/gallery")
async def list_gallery_apps(
    sort: str = Query(default="hot", description="정렬 기준: hot | latest | most_viewed | most_liked"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """공개(company) 앱 목록 + 뷰/좋아요 통계.

    일반 사용자에게는 visibility='company' 앱만 표시.
    관리자(admin)는 모든 앱을 볼 수 있다.

    sort 옵션:
      hot         — like_count*3 + dau*2 + unique_viewers (기본값)
      latest      — 최근 배포순
      most_viewed — 총 조회수 DESC
      most_liked  — 좋아요 수 DESC
    """
    requesting_role = current_user.get("role", "user")
    username = current_user["sub"]

    # ── 앱 목록 조회 (User JOIN으로 author 정보 포함) ──────────────────────
    query = (
        db.query(DeployedApp, User)
        .outerjoin(User, DeployedApp.owner_username == User.username)
        .filter(DeployedApp.status != "deleted")
    )
    if requesting_role != "admin":
        query = query.filter(
            (DeployedApp.visibility == "company")
            | (DeployedApp.owner_username == username)
        )
    rows = query.all()

    if not rows:
        return {"apps": []}

    apps = [row[0] for row in rows]
    user_by_app: dict[int, User] = {row[0].id: row[1] for row in rows}
    app_ids = [a.id for a in apps]

    # ── 뷰 통계 일괄 집계 (N+1 방지) ─────────────────────────────────────
    view_stats = (
        db.query(
            AppView.app_id,
            func.count(AppView.id).label("view_count"),
            func.count(func.distinct(AppView.viewer_user_id)).label("unique_viewers"),
        )
        .filter(AppView.app_id.in_(app_ids))
        .group_by(AppView.app_id)
        .all()
    )
    view_map: dict[int, tuple[int, int]] = {
        row.app_id: (row.view_count, row.unique_viewers) for row in view_stats
    }

    # ── DAU: 오늘 날짜 기준 고유 조회자 수 ──────────────────────────────
    today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
    dau_stats = (
        db.query(
            AppView.app_id,
            func.count(func.distinct(AppView.viewer_user_id)).label("dau"),
        )
        .filter(AppView.app_id.in_(app_ids), AppView.viewed_at >= today_start)
        .group_by(AppView.app_id)
        .all()
    )
    dau_map: dict[int, int] = {row.app_id: row.dau for row in dau_stats}

    # ── 좋아요 집계 (N+1 방지) ───────────────────────────────────────────
    like_stats = (
        db.query(
            AppLike.app_id,
            func.count(AppLike.id).label("like_count"),
        )
        .filter(AppLike.app_id.in_(app_ids))
        .group_by(AppLike.app_id)
        .all()
    )
    like_map: dict[int, int] = {row.app_id: row.like_count for row in like_stats}

    # ── 현재 사용자의 좋아요 여부 일괄 조회 ─────────────────────────────
    my_likes = (
        db.query(AppLike.app_id)
        .filter(AppLike.app_id.in_(app_ids), AppLike.user_id == username)
        .all()
    )
    liked_app_ids: set[int] = {row.app_id for row in my_likes}

    # ── 결과 조립 ────────────────────────────────────────────────────────
    results = []
    for app in apps:
        vc, uv = view_map.get(app.id, (0, 0))
        dau = dau_map.get(app.id, 0)
        like_count = like_map.get(app.id, 0)
        liked_by_me = app.id in liked_app_ids
        author: User | None = user_by_app.get(app.id)

        results.append({
            "id": app.id,
            "app_name": app.app_name,
            "app_url": app.app_url,
            "status": app.status,
            "visibility": app.visibility,
            "owner_username": app.owner_username,
            "author_name": author.name if author else None,
            "author_team": author.team_name if author else None,
            "author_region": author.region_name if author else None,
            "view_count": vc,
            "unique_viewers": uv,
            "dau": dau,
            "like_count": like_count,
            "liked_by_me": liked_by_me,
            "created_at": app.created_at.isoformat() if app.created_at else None,
            "updated_at": app.updated_at.isoformat() if app.updated_at else None,
        })

    # ── 정렬 ─────────────────────────────────────────────────────────────
    if sort == "hot":
        results.sort(
            key=lambda x: x["like_count"] * 3 + x["dau"] * 2 + x["unique_viewers"],
            reverse=True,
        )
    elif sort == "latest":
        results.sort(key=lambda x: x["created_at"] or "", reverse=True)
    elif sort == "most_viewed":
        results.sort(key=lambda x: x["view_count"], reverse=True)
    elif sort == "most_liked":
        results.sort(key=lambda x: x["like_count"], reverse=True)
    else:
        # 알 수 없는 sort 값은 hot과 동일하게 처리
        results.sort(
            key=lambda x: x["like_count"] * 3 + x["dau"] * 2 + x["unique_viewers"],
            reverse=True,
        )

    return {"apps": results}


# ==================== 좋아요 ====================


@router.post("/{app_name}/like")
async def toggle_app_like(
    app_name: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """앱 좋아요 토글. 이미 좋아요한 경우 취소, 아닌 경우 추가."""
    username = current_user["sub"]

    # 1. 앱 조회 (삭제되지 않은 것만)
    app = (
        db.query(DeployedApp)
        .filter(DeployedApp.app_name == app_name, DeployedApp.status != "deleted")
        .first()
    )
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="앱을 찾을 수 없습니다")

    # 2. 기존 좋아요 여부 확인
    existing_like = (
        db.query(AppLike)
        .filter(AppLike.app_id == app.id, AppLike.user_id == username)
        .first()
    )

    if existing_like:
        # 3a. 이미 좋아요 → 취소 (unlike)
        db.delete(existing_like)
        db.commit()
        liked = False
    else:
        # 3b. 좋아요 없음 → 추가 (동시 요청 시 IntegrityError 안전 처리)
        try:
            new_like = AppLike(app_id=app.id, user_id=username)
            db.add(new_like)
            db.commit()
            liked = True
        except IntegrityError:
            db.rollback()
            liked = True  # 이미 다른 요청에서 추가됨

    # 4. 총 좋아요 수 집계
    like_count = db.query(func.count(AppLike.id)).filter(AppLike.app_id == app.id).scalar()

    return {"liked": liked, "like_count": like_count}


# ==================== 공유 관리 (통합 조회/일괄 회수) ====================


@router.get("/my-shares")
async def list_my_shares(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """내가 공유한 모든 항목 (앱 ACL + 데이터셋 공유) 통합 조회."""
    username = current_user["sub"]

    # 1. 내 앱의 활성 ACL
    my_apps = (
        db.query(DeployedApp)
        .filter(
            DeployedApp.owner_username == username,
            DeployedApp.status != "deleted",
        )
        .all()
    )

    shares = []
    for app in my_apps:
        acls = (
            db.query(AppACL)
            .filter(AppACL.app_id == app.id, AppACL.revoked_at.is_(None))
            .all()
        )
        for acl in acls:
            # Skip self-grants
            if acl.grant_type == "user" and acl.grant_value.upper() == username.upper():
                continue
            shares.append({
                "id": acl.id,
                "type": "app",
                "resource_name": app.app_name,
                "resource_id": app.id,
                "grant_type": acl.grant_type,
                "grant_value": acl.grant_value,
                "granted_at": acl.granted_at.isoformat() if acl.granted_at else None,
            })

    # 2. 내 데이터셋의 활성 공유
    from app.models.file_share import SharedDataset, FileShareACL
    my_datasets = (
        db.query(SharedDataset)
        .filter(SharedDataset.owner_username == username)
        .all()
    )
    for ds in my_datasets:
        ds_acls = (
            db.query(FileShareACL)
            .filter(FileShareACL.dataset_id == ds.id, FileShareACL.revoked_at.is_(None))
            .all()
        )
        for acl in ds_acls:
            shares.append({
                "id": acl.id,
                "type": "dataset",
                "resource_name": ds.dataset_name,
                "resource_id": ds.id,
                "grant_type": getattr(acl, 'share_type', 'user'),
                "grant_value": getattr(acl, 'share_target', ''),
                "granted_at": acl.granted_at.isoformat() if hasattr(acl, 'granted_at') and acl.granted_at else None,
            })

    return {"shares": shares, "total": len(shares)}


@router.post("/my-shares/bulk-revoke")
async def bulk_revoke_shares(
    request: dict,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """선택한 공유 항목 일괄 회수."""
    username = current_user["sub"]
    items = request.get("items", [])

    revoked_count = 0
    from app.models.file_share import FileShareACL

    for item in items:
        item_type = item.get("type")
        item_id = item.get("id")

        if item_type == "app":
            acl = db.query(AppACL).filter(AppACL.id == item_id, AppACL.revoked_at.is_(None)).first()
            if acl:
                # Verify ownership
                app = db.query(DeployedApp).filter(DeployedApp.id == acl.app_id, DeployedApp.owner_username == username).first()
                if app:
                    acl.revoked_at = datetime.now(timezone.utc)
                    revoked_count += 1

        elif item_type == "dataset":
            ds_acl = db.query(FileShareACL).filter(
                FileShareACL.id == item_id,
                FileShareACL.revoked_at.is_(None)
            ).first()
            if ds_acl:
                from app.models.file_share import SharedDataset
                ds = db.query(SharedDataset).filter(SharedDataset.id == ds_acl.dataset_id, SharedDataset.owner_username == username).first()
                if ds:
                    ds_acl.revoked_at = datetime.now(timezone.utc)
                    revoked_count += 1

    db.commit()
    return {"revoked": revoked_count}


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

    # 사용자 조회 + slug 확인 (사번 비노출 URL용)
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    slug = user.app_slug
    if not slug:
        # Auto-generate if missing
        from app.core.security import generate_app_slug
        slug = generate_app_slug(username)
        user.app_slug = slug
        db.commit()

    # 배포 권한 확인
    _require_deploy_permission(username, db)

    # 앱 이름 유효성 검사
    _validate_app_name(request.app_name)

    # 포트 범위 + 블랙리스트 검증 (SSRF 방지)
    if not (_ALLOWED_PORT_MIN <= request.app_port <= _ALLOWED_PORT_MAX):
        raise HTTPException(
            status_code=400,
            detail=f"앱 포트는 {_ALLOWED_PORT_MIN}-{_ALLOWED_PORT_MAX} 범위만 허용됩니다.",
        )
    if request.app_port in _BLOCKED_PORTS:
        raise HTTPException(
            status_code=400,
            detail=f"포트 {request.app_port}는 보안상 사용할 수 없습니다.",
        )

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
        existing_app.visibility = request.visibility
        existing_app.app_port = request.app_port
        existing_app.status = "running"
        existing_app.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing_app)
        # 재배포: 기존 K8s 리소스 삭제 후 재생성
        try:
            from app.services.app_deploy_service import AppDeployService

            deploy_svc = AppDeployService(settings)
            pod_name = AppDeployService._app_pod_name(slug, request.app_name)
            deploy_svc._delete_app_resources(pod_name)
            deploy_svc._create_app_pod(
                pod_name,
                username,
                request.app_name,
                request.version or "v1",
                user.security_policy,
                request.app_port,
            )
            deploy_svc._create_app_service(pod_name, username, request.app_name, request.app_port)
            deploy_svc._create_app_ingress(pod_name, slug, request.app_name, request.app_port)
            logger.info(f"K8s resources recreated for {pod_name}")
        except Exception as e:
            logger.error(f"K8s redeploy failed for {request.app_name}: {e}")
            # DB 레코드는 유지, status를 inactive로 변경
            existing_app.status = "inactive"
            db.commit()

        logger.info(f"App redeployed: {username}/{request.app_name} v={request.version}")
        return DeployedAppResponse.model_validate(existing_app)

    # 신규 배포
    from app.services.app_deploy_service import AppDeployService
    app_url = AppDeployService._app_url(slug, request.app_name)
    pod_name = AppDeployService._app_pod_name(slug, request.app_name)

    # soft-deleted 앱 확인 (재활성화 대상) — UNIQUE constraint 충돌 방지
    deleted_app = (
        db.query(DeployedApp)
        .filter(
            DeployedApp.owner_username == username,
            DeployedApp.app_name == request.app_name,
            DeployedApp.status == "deleted",
        )
        .first()
    )
    if deleted_app:
        # 기존 soft-deleted 레코드를 재활성화
        deleted_app.status = "running"
        deleted_app.version = request.version
        deleted_app.visibility = request.visibility
        deleted_app.app_port = request.app_port
        deleted_app.app_url = app_url
        deleted_app.pod_name = pod_name
        deleted_app.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(deleted_app)
        # 재활성화: K8s 리소스 생성
        try:
            deploy_svc = AppDeployService(settings)
            deploy_svc._create_app_pod(
                pod_name,
                username,
                request.app_name,
                request.version or "v1",
                user.security_policy,
                request.app_port,
            )
            deploy_svc._create_app_service(pod_name, username, request.app_name, request.app_port)
            deploy_svc._create_app_ingress(pod_name, slug, request.app_name, request.app_port)
            logger.info(f"K8s resources created for reactivated app {pod_name}")
        except Exception as e:
            logger.error(f"K8s deploy failed for reactivated {request.app_name}: {e}")
            deleted_app.status = "inactive"
            db.commit()

        logger.info(f"App reactivated: {username}/{request.app_name} v={request.version}")
        return DeployedAppResponse.model_validate(deleted_app)

    new_app = DeployedApp(
        owner_username=username,
        app_name=request.app_name,
        app_url=app_url,
        pod_name=pod_name,
        status="running",
        version=request.version,
        visibility=request.visibility,
        app_port=request.app_port,
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
                    grant_type="user",
                    grant_value=acl_user,
                    granted_by=username,
                )
                db.add(acl)
        db.commit()

    # K8s 리소스 생성 (신규 배포만 — 재배포는 위에서 처리)
    try:
        deploy_svc = AppDeployService(settings)
        deploy_svc._create_app_pod(
            pod_name,
            username,
            request.app_name,
            request.version or "v1",
            user.security_policy,
            request.app_port,
        )
        deploy_svc._create_app_service(pod_name, username, request.app_name, request.app_port)
        deploy_svc._create_app_ingress(pod_name, slug, request.app_name, request.app_port)
        logger.info(f"K8s resources created for {pod_name}")
    except Exception as e:
        logger.error(f"K8s deploy failed for {request.app_name}: {e}")
        # DB 레코드는 유지, status를 inactive로 변경
        new_app.status = "inactive"
        db.commit()

    logger.info(f"App deployed: {username}/{request.app_name} v={request.version}")
    return DeployedAppResponse.model_validate(new_app)


@router.delete("/{app_name}", status_code=200)
async def undeploy_app(
    app_name: str,
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """앱 삭제 (소유자만 가능). 상태를 deleted로 변경하여 소프트 삭제 + K8s 리소스 삭제."""
    username = current_user["sub"]
    app = _get_owned_app(app_name, username, db)

    app.status = "deleted"
    app.updated_at = datetime.now(timezone.utc)
    db.commit()

    # K8s 리소스 삭제 (Pod + Service + Ingress)
    try:
        from app.services.app_deploy_service import AppDeployService

        deploy_svc = AppDeployService(settings)
        deploy_svc._delete_app_resources(app.pod_name)
        logger.info(f"K8s resources deleted for {app.pod_name}")
    except Exception as e:
        logger.error(f"K8s undeploy failed for {app.pod_name}: {e}")

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
    """앱 접근 권한 목록 (소유자만 조회 가능)."""
    username = current_user["sub"]
    app = _get_owned_app(app_name, username, db)

    # 활성 ACL 목록
    acl_entries = (
        db.query(AppACL)
        .filter(
            AppACL.app_id == app.id,
            AppACL.revoked_at.is_(None),
        )
        .order_by(AppACL.granted_at.desc())
        .all()
    )

    # grant_type=user인 항목의 사용자 정보 일괄 조회 (display_label용)
    user_grant_values = [e.grant_value for e in acl_entries if e.grant_type == "user"]
    users_map = {}
    if user_grant_values:
        users = db.query(User).filter(User.username.in_(user_grant_values)).all()
        users_map = {u.username: u for u in users}

    results = []
    for entry in acl_entries:
        # display_label 생성
        if entry.grant_type == "company":
            display_label = "전사 공개"
        elif entry.grant_type == "user":
            user = users_map.get(entry.grant_value)
            display_label = f"{entry.grant_value} ({user.name})" if user and user.name else entry.grant_value
        else:
            # team, region, job
            type_labels = {"team": "팀", "region": "지역", "job": "직책"}
            label = type_labels.get(entry.grant_type, entry.grant_type)
            display_label = f"{entry.grant_value} ({label})"

        results.append(AppACLDetailResponse(
            id=entry.id,
            app_id=entry.app_id,
            grant_type=entry.grant_type,
            grant_value=entry.grant_value,
            granted_by=entry.granted_by,
            display_label=display_label,
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
    """앱 접근 권한 부여 (소유자만 가능, 5-type grant).

    grant_type: user | team | region | job | company
    중복 방지: 동일 grant_type+grant_value 활성 ACL이 있으면 409 반환.
    """
    username = current_user["sub"]
    app = _get_owned_app(app_name, username, db)

    grant_type = request.grant_type.lower()
    grant_value = request.grant_value.strip()

    # grant_type 유효성 검사
    valid_grant_types = {"user", "team", "region", "job", "company"}
    if grant_type not in valid_grant_types:
        raise HTTPException(
            status_code=400,
            detail=f"grant_type은 {', '.join(sorted(valid_grant_types))} 중 하나여야 합니다",
        )

    # company 타입은 grant_value를 "*"로 정규화
    if grant_type == "company":
        grant_value = "*"

    # user 타입: 자기 자신에게 권한 부여 방지
    if grant_type == "user" and grant_value.upper() == username:
        raise HTTPException(
            status_code=400,
            detail="앱 소유자는 이미 접근 권한이 있습니다",
        )

    # user 타입: 대상 사용자 존재 + 승인 여부 확인
    if grant_type == "user":
        grant_value = grant_value.upper()  # 사번은 대문자 정규화
        target_user = (
            db.query(User)
            .filter(User.username == grant_value, User.is_approved == True)  # noqa: E712
            .first()
        )
        if not target_user:
            raise HTTPException(
                status_code=404,
                detail="승인된 사용자를 찾을 수 없습니다",
            )

    # 활성 ACL 중복 확인 (동일 grant_type + grant_value)
    existing_acl = (
        db.query(AppACL)
        .filter(
            AppACL.app_id == app.id,
            AppACL.grant_type == grant_type,
            AppACL.grant_value == grant_value,
            AppACL.revoked_at.is_(None),
        )
        .first()
    )
    if existing_acl:
        raise HTTPException(
            status_code=409,
            detail="이미 동일한 접근 권한이 부여되어 있습니다",
        )

    # ACL 생성
    acl = AppACL(
        app_id=app.id,
        grant_type=grant_type,
        grant_value=grant_value,
        granted_by=username,
    )
    db.add(acl)
    db.commit()
    db.refresh(acl)

    logger.info(f"ACL granted: {grant_type}={grant_value} → {username}/{app_name}")
    return AppACLResponse.model_validate(acl)


@router.delete("/{app_name}/acl/{acl_id}", status_code=200)
async def revoke_app_access(
    app_name: str,
    acl_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """앱 접근 권한 회수 (소유자만 가능, ACL ID 기반). revoked_at 설정으로 소프트 삭제."""
    username = current_user["sub"]
    app = _get_owned_app(app_name, username, db)

    acl = (
        db.query(AppACL)
        .filter(
            AppACL.id == acl_id,
            AppACL.app_id == app.id,
            AppACL.revoked_at.is_(None),
        )
        .first()
    )
    if not acl:
        raise HTTPException(
            status_code=404,
            detail="해당 활성 접근 권한을 찾을 수 없습니다",
        )

    acl.revoked_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(f"ACL revoked: {acl.grant_type}={acl.grant_value} from {username}/{app_name}")
    return {"revoked": True, "acl_id": acl_id, "grant_type": acl.grant_type, "grant_value": acl.grant_value, "app_name": app_name}


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
