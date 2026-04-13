"""GitHub #25 — 4xx/5xx 커스텀 에러 페이지 렌더링.

Accept 기반 분기: 브라우저(text/html)는 /error?code=... 302 리다이렉트,
API 클라이언트는 JSON 유지.
"""

import os

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException


os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SSO_AUTH_URL", "https://stub/auth")
os.environ.setdefault("SSO_AUTH_URL2", "https://stub/userinfo")


def _build_app():
    """main.py의 핵심 핸들러만 복제한 최소 FastAPI 앱 — 컨테이너 mount 없이 단일 파일 테스트."""
    app = FastAPI()

    _HTML_ERROR_CODES = {404, 502, 503, 504}

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        path = request.url.path
        accept = request.headers.get("accept", "").lower()

        want_html = (
            exc.status_code in _HTML_ERROR_CODES
            and "text/html" in accept
            and not path.startswith("/api/")
            and not path.startswith("/auth/")
            and not path.startswith("/error")
            and not path.startswith("/static/")
        )
        if want_html:
            from urllib.parse import quote
            original_uri = quote(path, safe="")
            return RedirectResponse(
                url=f"/error?code={exc.status_code}&uri={original_uri}",
                status_code=302,
            )
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None) or None,
        )

    @app.get("/error")
    async def error_page():
        return JSONResponse({"error_page": True})

    @app.get("/api/ping")
    async def api_ping():
        raise HTTPException(status_code=404, detail="not found")

    @app.get("/auth/missing")
    async def auth_missing():
        raise HTTPException(status_code=503, detail="auth svc down")

    @app.get("/some-missing")
    async def some_missing():
        raise HTTPException(status_code=404, detail="not found")

    @app.get("/some-503")
    async def some_503():
        raise HTTPException(status_code=503, detail="gone")

    @app.get("/unauthorized")
    async def unauthorized():
        raise HTTPException(status_code=401, detail="need auth")

    return app


@pytest.fixture()
def client():
    return TestClient(_build_app(), follow_redirects=False)


class TestBrowserHtmlRedirect:
    def test_404_html_redirects_to_error_page(self, client):
        resp = client.get("/some-missing", headers={"Accept": "text/html"})
        assert resp.status_code == 302
        assert "/error?code=404" in resp.headers["location"]
        assert "uri=%2Fsome-missing" in resp.headers["location"]

    def test_503_html_redirects(self, client):
        resp = client.get("/some-503", headers={"Accept": "text/html,application/xhtml+xml"})
        assert resp.status_code == 302
        assert "/error?code=503" in resp.headers["location"]


class TestApiJsonPreserved:
    def test_api_path_returns_json_even_with_html_accept(self, client):
        resp = client.get("/api/ping", headers={"Accept": "text/html"})
        assert resp.status_code == 404
        assert resp.json() == {"detail": "not found"}

    def test_auth_path_returns_json(self, client):
        resp = client.get("/auth/missing", headers={"Accept": "text/html"})
        assert resp.status_code == 503
        assert resp.json() == {"detail": "auth svc down"}

    def test_json_accept_returns_json(self, client):
        resp = client.get("/some-missing", headers={"Accept": "application/json"})
        assert resp.status_code == 404
        assert resp.json() == {"detail": "not found"}

    def test_no_accept_defaults_to_json(self, client):
        resp = client.get("/some-missing")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "not found"}


class TestUnaffectedStatusCodes:
    def test_401_keeps_json_even_for_browser(self, client):
        """401은 기존 인증 플로우 담당 — 리다이렉트 대상 아님."""
        resp = client.get("/unauthorized", headers={"Accept": "text/html"})
        assert resp.status_code == 401
        assert resp.json() == {"detail": "need auth"}
