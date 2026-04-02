"""Tests for multi-port support in app deployment and proxy.

Covers:
  - Deploying with a custom port (e.g. 8501 for Streamlit)
  - Default port is 3000
  - Proxy constructs target URL using the DB-stored port
"""

from unittest.mock import AsyncMock, patch

import httpx

from app.models.app import DeployedApp


def test_deploy_app_with_custom_port(client, create_test_user):
    """POST /api/v1/apps/deploy with app_port=8501 stores the port."""
    create_test_user(username="TESTUSER01", can_deploy_apps=True)

    resp = client.post("/api/v1/apps/deploy", json={
        "app_name": "streamlit-app",
        "version": "v1",
        "app_port": 8501,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["app_port"] == 8501


def test_deploy_app_default_port_is_3000(client, create_test_user):
    """When app_port is omitted, it defaults to 3000."""
    create_test_user(username="TESTUSER01", can_deploy_apps=True)

    resp = client.post("/api/v1/apps/deploy", json={
        "app_name": "default-port-app",
        "version": "v1",
    })
    assert resp.status_code == 201
    assert resp.json()["app_port"] == 3000


def test_proxy_uses_db_port(client, db_session, create_test_app, test_settings):
    """The proxy should build the target URL using the app's stored port.

    We mock httpx.AsyncClient.request to capture the URL it receives,
    and also mock _get_user_from_request so the proxy sees an authenticated user.
    """
    app_row = create_test_app(
        owner_username="PROXYUSER",
        app_name="custom-port",
        app_port=5000,
    )
    pod_name = app_row.pod_name  # "app-proxyuser-custom-port"

    captured_urls: list[str] = []

    async def _fake_request(self, *, method, url, headers, content):
        """Capture the target URL and return a minimal response."""
        captured_urls.append(url)
        return httpx.Response(200, text="ok")

    fake_user = {"sub": "PROXYUSER", "name": "Proxy User"}

    with (
        patch("httpx.AsyncClient.request", new=_fake_request),
        patch(
            "app.routers.app_proxy._get_user_from_request",
            new=AsyncMock(return_value=fake_user),
        ),
    ):
        resp = client.get(f"/app/{pod_name}/index.html")

    assert resp.status_code == 200
    assert len(captured_urls) == 1
    # Target URL must contain port 5000, not the default 3000
    assert ":5000/" in captured_urls[0]
    assert pod_name in captured_urls[0]
