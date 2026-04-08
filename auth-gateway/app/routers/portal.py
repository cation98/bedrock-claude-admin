"""웹앱 관리 포털 API.

Endpoints:
  GET  /portal/                    -- 포털 HTML 페이지
  GET  /api/v1/portal/my-apps      -- 내 앱 목록 (DAU/MAU 포함)
  GET  /api/v1/portal/apps/{name}/stats -- 앱 통계 (DAU, MAU, 최근 접속자)
  GET  /api/v1/portal/acl-options  -- ACL 드롭다운 옵션 (teams, regions, jobs)
"""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, distinct, and_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.app import DeployedApp, AppView, AppACL
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portal"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/portal")
@router.get("/portal/")
@router.get("/portal/{path:path}")
async def serve_portal():
    """포털 SPA HTML 반환."""
    html_path = STATIC_DIR / "portal.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Portal page not found")
    return FileResponse(html_path, media_type="text/html")


@router.get("/api/v1/portal/my-apps")
async def portal_my_apps(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """포털용 내 앱 목록 (DAU/MAU 뱃지 포함)."""
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

    if not apps:
        return {"apps": []}

    today = datetime.now(timezone.utc).date()
    month_start = today.replace(day=1)
    app_ids = [a.id for a in apps]

    # DAU (오늘)
    dau_rows = (
        db.query(AppView.app_id, func.count(distinct(AppView.viewer_user_id)))
        .filter(AppView.app_id.in_(app_ids), func.date(AppView.viewed_at) == today)
        .group_by(AppView.app_id)
        .all()
    )
    dau_map = {r[0]: r[1] for r in dau_rows}

    # MAU (이번 달)
    mau_rows = (
        db.query(AppView.app_id, func.count(distinct(AppView.viewer_user_id)))
        .filter(AppView.app_id.in_(app_ids), AppView.viewed_at >= datetime.combine(month_start, datetime.min.time()).replace(tzinfo=timezone.utc))
        .group_by(AppView.app_id)
        .all()
    )
    mau_map = {r[0]: r[1] for r in mau_rows}

    # ACL count per app
    acl_rows = (
        db.query(AppACL.app_id, func.count(AppACL.id))
        .filter(AppACL.app_id.in_(app_ids), AppACL.revoked_at.is_(None))
        .group_by(AppACL.app_id)
        .all()
    )
    acl_map = {r[0]: r[1] for r in acl_rows}

    results = []
    for app in apps:
        results.append({
            "id": app.id,
            "app_name": app.app_name,
            "app_url": app.app_url,
            "status": app.status,
            "version": app.version,
            "visibility": app.visibility,
            "app_port": app.app_port,
            "dau": dau_map.get(app.id, 0),
            "mau": mau_map.get(app.id, 0),
            "acl_count": acl_map.get(app.id, 0),
            "created_at": app.created_at.isoformat() if app.created_at else None,
            "updated_at": app.updated_at.isoformat() if app.updated_at else None,
        })

    return {"apps": results}


@router.get("/api/v1/portal/apps/{app_name}/stats")
async def portal_app_stats(
    app_name: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """앱 상세 통계 — DAU(30일), MAU, 최근 접속자."""
    username = current_user["sub"]
    role = current_user.get("role", "user")

    app = (
        db.query(DeployedApp)
        .filter(DeployedApp.app_name == app_name, DeployedApp.status != "deleted")
        .first()
    )
    if not app:
        raise HTTPException(status_code=404, detail="앱을 찾을 수 없습니다")
    if app.owner_username != username and role != "admin":
        raise HTTPException(status_code=403, detail="앱 소유자만 통계를 볼 수 있습니다")

    today = datetime.now(timezone.utc).date()
    month_start = today.replace(day=1)
    thirty_days_ago = today - timedelta(days=30)

    # DAU 시계열 (최근 30일)
    dau_series = (
        db.query(
            func.date(AppView.viewed_at).label("day"),
            func.count(distinct(AppView.viewer_user_id)).label("visitors"),
        )
        .filter(
            AppView.app_id == app.id,
            AppView.viewed_at >= datetime.combine(thirty_days_ago, datetime.min.time()).replace(tzinfo=timezone.utc),
        )
        .group_by(func.date(AppView.viewed_at))
        .order_by(func.date(AppView.viewed_at))
        .all()
    )

    # MAU
    mau = (
        db.query(func.count(distinct(AppView.viewer_user_id)))
        .filter(
            AppView.app_id == app.id,
            AppView.viewed_at >= datetime.combine(month_start, datetime.min.time()).replace(tzinfo=timezone.utc),
        )
        .scalar()
    ) or 0

    # 총 조회수
    total_views = db.query(func.count(AppView.id)).filter(AppView.app_id == app.id).scalar() or 0

    # 최근 접속자 (최근 20명, 중복 제거)
    recent_subq = (
        db.query(
            AppView.viewer_user_id,
            func.max(AppView.viewed_at).label("last_visit"),
        )
        .filter(AppView.app_id == app.id)
        .group_by(AppView.viewer_user_id)
        .order_by(func.max(AppView.viewed_at).desc())
        .limit(20)
        .subquery()
    )

    recent_visitors_raw = (
        db.query(recent_subq.c.viewer_user_id, recent_subq.c.last_visit, User.name, User.team_name)
        .outerjoin(User, User.username == recent_subq.c.viewer_user_id)
        .order_by(recent_subq.c.last_visit.desc())
        .all()
    )

    recent_visitors = [
        {
            "username": r[0],
            "name": r[2] or r[0],
            "team_name": r[3] or "",
            "visited_at": r[1].isoformat() if r[1] else None,
        }
        for r in recent_visitors_raw
    ]

    return {
        "app_id": app.id,
        "app_name": app.app_name,
        "dau_series": [{"date": str(r.day), "visitors": r.visitors} for r in dau_series],
        "dau_today": next((r.visitors for r in dau_series if str(r.day) == str(today)), 0),
        "mau": mau,
        "total_views": total_views,
        "recent_visitors": recent_visitors,
    }


@router.get("/api/v1/portal/acl-options")
async def portal_acl_options(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ACL 드롭다운 옵션 — 시스템에 등록된 팀/지역/직책 목록."""
    teams = [r[0] for r in db.query(distinct(User.team_name)).filter(User.team_name.isnot(None), User.team_name != "").order_by(User.team_name).all()]
    regions = [r[0] for r in db.query(distinct(User.region_name)).filter(User.region_name.isnot(None), User.region_name != "").order_by(User.region_name).all()]
    jobs = [r[0] for r in db.query(distinct(User.job_name)).filter(User.job_name.isnot(None), User.job_name != "").order_by(User.job_name).all()]

    return {"teams": teams, "regions": regions, "jobs": jobs}
