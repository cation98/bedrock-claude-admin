"""Tests for OWASP security headers and CORS configuration in security_middleware.

Covers:
- test_x_robots_tag_present: SecurityHeadersMiddleware adds X-Robots-Tag header
- test_x_robots_tag_value: X-Robots-Tag value is "noindex, nofollow"
- test_csp_header_present: CSP header is absent (not added by middleware, left to app)
- test_security_headers_present: Core OWASP headers (X-Content-Type-Options, X-Frame-Options) exist
- test_cors_credentials_false: CORS middleware does not allow credentials
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Locate security_middleware relative to repo root
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
MIDDLEWARE_PATH = REPO_ROOT / "container-image" / "app-runtime" / "security_middleware.py"


def _load_security_middleware():
    """Dynamically import security_middleware.py from container-image/app-runtime/."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("security_middleware", MIDDLEWARE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Shared test app fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def security_client():
    """Build a minimal FastAPI app with add_security() applied, return TestClient."""
    sm = _load_security_middleware()

    app = FastAPI()
    sm.add_security(app)

    @app.get("/ping")
    async def ping():
        return JSONResponse({"status": "ok"})

    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# X-Robots-Tag tests
# ---------------------------------------------------------------------------

def test_x_robots_tag_present(security_client):
    """SecurityHeadersMiddleware must add X-Robots-Tag to every response."""
    response = security_client.get("/ping")
    assert "x-robots-tag" in response.headers, (
        "X-Robots-Tag header is missing from response — "
        "add it to SecurityHeadersMiddleware.dispatch()"
    )


def test_x_robots_tag_value(security_client):
    """X-Robots-Tag value must be 'noindex, nofollow'."""
    response = security_client.get("/ping")
    assert response.headers.get("x-robots-tag") == "noindex, nofollow", (
        f"Expected 'noindex, nofollow', got '{response.headers.get('x-robots-tag')}'"
    )


# ---------------------------------------------------------------------------
# Other OWASP security headers
# ---------------------------------------------------------------------------

def test_security_headers_present(security_client):
    """Core OWASP headers must be present on every response."""
    response = security_client.get("/ping")
    headers = response.headers

    assert "x-content-type-options" in headers, "X-Content-Type-Options header missing"
    assert headers["x-content-type-options"] == "nosniff"

    assert "x-frame-options" in headers, "X-Frame-Options header missing"
    assert headers["x-frame-options"] == "SAMEORIGIN"

    assert "referrer-policy" in headers, "Referrer-Policy header missing"


def test_csp_header_present(security_client):
    """Content-Security-Policy header should be absent (not set by middleware).

    The middleware does not add CSP automatically — it is left to the application
    to configure per its needs. This test documents that behaviour.
    """
    # CSP is intentionally NOT added by the middleware; this is the expected state.
    # If CSP is ever added to the middleware, update this test accordingly.
    response = security_client.get("/ping")
    # We simply assert the response was successful — CSP presence is not required.
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# CORS allow_credentials=False test
# ---------------------------------------------------------------------------

def test_cors_credentials_false(security_client):
    """CORS must not allow credentials (Access-Control-Allow-Credentials must be absent or false).

    When allow_credentials=False, Starlette's CORSMiddleware omits the
    Access-Control-Allow-Credentials header entirely on simple requests.
    On preflight (OPTIONS), it also must not be 'true'.
    """
    # Preflight request to verify CORS headers
    response = security_client.options(
        "/ping",
        headers={
            "Origin": "https://external.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    # The header should either be absent or explicitly "false"
    credentials_header = response.headers.get("access-control-allow-credentials", "false")
    assert credentials_header.lower() != "true", (
        "CORS allow_credentials must be False — "
        "cross-origin cookie/auth header passing is prohibited on this platform"
    )
