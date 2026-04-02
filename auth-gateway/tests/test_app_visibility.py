"""Tests for app visibility (private vs company) and auth-check ACL logic.

Covers:
  - Deploying with explicit visibility='company'
  - Default visibility is 'private'
  - company visibility allows any authenticated SSO user
  - private visibility requires ACL entry
  - private visibility always allows the app owner
  - Backward compat: visibility unset defaults to private
"""

from app.core.security import create_access_token
from app.models.app import AppACL


# --------------- helpers ---------------

def _auth_check_headers(token: str, original_uri: str) -> dict:
    """Build headers that mimic NGINX Ingress auth-url callback."""
    return {
        "Authorization": f"Bearer {token}",
        "X-Original-URI": original_uri,
    }


def _make_token(settings, sub: str, role: str = "user") -> str:
    return create_access_token({"sub": sub, "role": role}, settings=settings)


# --------------- tests ---------------


def test_deploy_app_with_visibility_company(client, create_test_user, test_settings):
    """POST /api/v1/apps/deploy with visibility='company' persists correctly."""
    create_test_user(username="TESTUSER01", can_deploy_apps=True)

    resp = client.post("/api/v1/apps/deploy", json={
        "app_name": "public-app",
        "version": "v1",
        "visibility": "company",
        "app_port": 3000,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["visibility"] == "company"


def test_deploy_app_default_visibility_is_private(client, create_test_user):
    """When visibility is omitted, it defaults to 'private'."""
    create_test_user(username="TESTUSER01", can_deploy_apps=True)

    resp = client.post("/api/v1/apps/deploy", json={
        "app_name": "secret-app",
        "version": "v1",
    })
    assert resp.status_code == 201
    assert resp.json()["visibility"] == "private"


def test_auth_check_company_allows_any_sso_user(
    client, db_session, create_test_user, create_test_app, test_settings,
):
    """A company-visible app should allow any authenticated user (not just ACL)."""
    create_test_user(username="OWNER01", can_deploy_apps=True)
    create_test_app(
        owner_username="OWNER01",
        app_name="open-app",
        visibility="company",
    )

    # A different user with a valid token — no ACL entry
    other_token = _make_token(test_settings, sub="VISITOR01")

    resp = client.get(
        "/api/v1/apps/auth-check",
        headers=_auth_check_headers(other_token, "/apps/OWNER01/open-app/"),
    )
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True


def test_auth_check_private_requires_acl(
    client, db_session, create_test_user, create_test_app, test_settings,
):
    """A private app should deny access to a user without an ACL entry."""
    create_test_user(username="OWNER01", can_deploy_apps=True)
    create_test_app(
        owner_username="OWNER01",
        app_name="locked-app",
        visibility="private",
    )

    visitor_token = _make_token(test_settings, sub="VISITOR01")
    resp = client.get(
        "/api/v1/apps/auth-check",
        headers=_auth_check_headers(visitor_token, "/apps/OWNER01/locked-app/"),
    )
    assert resp.status_code == 403


def test_auth_check_private_owner_always_allowed(
    client, db_session, create_test_user, create_test_app, test_settings,
):
    """The owner of a private app should always pass auth-check."""
    create_test_user(username="OWNER01", can_deploy_apps=True)
    create_test_app(
        owner_username="OWNER01",
        app_name="locked-app",
        visibility="private",
    )

    owner_token = _make_token(test_settings, sub="OWNER01")
    resp = client.get(
        "/api/v1/apps/auth-check",
        headers=_auth_check_headers(owner_token, "/apps/OWNER01/locked-app/"),
    )
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True


def test_visibility_unset_defaults_to_private(
    client, db_session, create_test_user, create_test_app, test_settings,
):
    """An app created without explicit visibility should behave as 'private'.

    This tests backward compatibility: older rows may have visibility=NULL
    or the default 'private' value. Either way, non-owner non-ACL users
    should be denied.
    """
    create_test_user(username="OWNER01", can_deploy_apps=True)
    # create_test_app defaults visibility to 'private'
    create_test_app(
        owner_username="OWNER01",
        app_name="legacy-app",
    )

    visitor_token = _make_token(test_settings, sub="VISITOR01")
    resp = client.get(
        "/api/v1/apps/auth-check",
        headers=_auth_check_headers(visitor_token, "/apps/OWNER01/legacy-app/"),
    )
    # private + no ACL → 403
    assert resp.status_code == 403
