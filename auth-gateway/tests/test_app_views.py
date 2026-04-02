"""Tests for view counting (AppView recording and gallery aggregation).

Covers:
  - View recorded on successful auth-check
  - Static assets (.css, .js, .png) are NOT recorded
  - Gallery endpoint returns view_count
  - Gallery endpoint returns unique_viewers
  - View insert failure does not break the proxy/auth-check
"""

import time
from unittest.mock import patch, MagicMock

from app.core.security import create_access_token
from app.models.app import AppACL, AppView


# --------------- helpers ---------------

def _auth_check_headers(token: str, original_uri: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-Original-URI": original_uri,
    }


def _make_token(settings, sub: str, role: str = "user") -> str:
    return create_access_token({"sub": sub, "role": role}, settings=settings)


def _drain_tasks():
    """Give background asyncio tasks time to complete."""
    time.sleep(0.2)


# --------------- tests ---------------


def test_view_recorded_on_auth_check_success(
    client, db_session, create_test_user, create_test_app, test_settings,
):
    """A successful auth-check for a company-visible app should create an AppView row.

    _record_view creates its own SessionLocal() internally, so we patch
    app.routers.apps.SessionLocal to return our test session factory.
    We also need to expire the db_session cache to see the new row.
    """
    from tests.conftest import TestSessionLocal

    create_test_user(username="OWNER01", can_deploy_apps=True)
    app_row = create_test_app(
        owner_username="OWNER01",
        app_name="viewed-app",
        visibility="company",
    )

    visitor_token = _make_token(test_settings, sub="VIEWER01")

    # Patch SessionLocal in the apps module so _record_view writes to our test DB
    with patch("app.routers.apps.SessionLocal", TestSessionLocal):
        resp = client.get(
            "/api/v1/apps/auth-check",
            headers=_auth_check_headers(visitor_token, "/apps/OWNER01/viewed-app/"),
        )
        assert resp.status_code == 200
        _drain_tasks()

    # Expire cached objects so the query hits the DB fresh
    db_session.expire_all()
    views = db_session.query(AppView).filter(AppView.app_id == app_row.id).all()
    assert len(views) >= 1
    assert views[0].viewer_user_id == "VIEWER01"


def test_static_asset_not_recorded(
    client, db_session, create_test_user, create_test_app, test_settings,
):
    """Static asset URIs (.css, .js, .png) should NOT create AppView rows."""
    from tests.conftest import TestSessionLocal

    create_test_user(username="OWNER01", can_deploy_apps=True)
    app_row = create_test_app(
        owner_username="OWNER01",
        app_name="static-test",
        visibility="company",
    )

    visitor_token = _make_token(test_settings, sub="VIEWER01")

    static_uris = [
        "/apps/OWNER01/static-test/style.css",
        "/apps/OWNER01/static-test/bundle.js",
        "/apps/OWNER01/static-test/logo.png",
    ]

    with patch("app.routers.apps.SessionLocal", TestSessionLocal):
        for uri in static_uris:
            resp = client.get(
                "/api/v1/apps/auth-check",
                headers=_auth_check_headers(visitor_token, uri),
            )
            assert resp.status_code == 200
        _drain_tasks()

    db_session.expire_all()
    views = db_session.query(AppView).filter(AppView.app_id == app_row.id).all()
    assert len(views) == 0, f"Expected 0 views for static assets, got {len(views)}"


def test_gallery_returns_view_counts(
    client, db_session, create_test_user, create_test_app,
):
    """GET /api/v1/apps/gallery should include view_count for each app."""
    create_test_user(username="TESTUSER01", can_deploy_apps=True)
    app_row = create_test_app(owner_username="TESTUSER01", app_name="gallery-app")

    # Insert view rows directly
    for viewer in ["V001", "V002", "V001"]:  # 3 total, 2 unique
        db_session.add(AppView(app_id=app_row.id, viewer_user_id=viewer))
    db_session.commit()

    resp = client.get("/api/v1/apps/gallery")
    assert resp.status_code == 200
    data = resp.json()["apps"]
    assert len(data) >= 1

    target = next(a for a in data if a["app_name"] == "gallery-app")
    assert target["view_count"] == 3


def test_gallery_returns_unique_viewers(
    client, db_session, create_test_user, create_test_app,
):
    """GET /api/v1/apps/gallery should include unique_viewers for each app."""
    create_test_user(username="TESTUSER01", can_deploy_apps=True)
    app_row = create_test_app(owner_username="TESTUSER01", app_name="unique-app")

    for viewer in ["V001", "V002", "V001", "V003"]:  # 4 total, 3 unique
        db_session.add(AppView(app_id=app_row.id, viewer_user_id=viewer))
    db_session.commit()

    resp = client.get("/api/v1/apps/gallery")
    assert resp.status_code == 200
    data = resp.json()["apps"]

    target = next(a for a in data if a["app_name"] == "unique-app")
    assert target["unique_viewers"] == 3


def test_view_insert_failure_does_not_break_proxy(
    client, db_session, create_test_user, create_test_app, test_settings,
):
    """If _record_view raises an exception, auth-check should still return 200.

    The _maybe_record_view wrapper catches all exceptions to avoid
    breaking the auth-check response.
    """
    create_test_user(username="OWNER01", can_deploy_apps=True)
    create_test_app(
        owner_username="OWNER01",
        app_name="fail-view-app",
        visibility="company",
    )

    visitor_token = _make_token(test_settings, sub="VIEWER01")

    # Patch _record_view to raise an exception
    with patch(
        "app.routers.apps._record_view",
        side_effect=Exception("DB write failed"),
    ):
        resp = client.get(
            "/api/v1/apps/auth-check",
            headers=_auth_check_headers(
                visitor_token, "/apps/OWNER01/fail-view-app/dashboard"
            ),
        )

    # auth-check must still succeed despite the view recording failure
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True
