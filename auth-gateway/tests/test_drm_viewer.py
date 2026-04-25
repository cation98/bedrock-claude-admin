"""Tests for file viewer type routing (Task 1 — Bundle 6)."""

import pytest

from app.services.file_viewer_router import ViewerType, get_viewer_type


@pytest.mark.parametrize("filename,expected", [
    ("report.pdf",   ViewerType.ONLYOFFICE),
    ("data.xlsx",    ViewerType.ONLYOFFICE),
    ("slides.pptx",  ViewerType.ONLYOFFICE),
    ("notes.docx",   ViewerType.ONLYOFFICE),
    ("data.csv",     ViewerType.ONLYOFFICE),
    ("readme.txt",   ViewerType.ONLYOFFICE),
])
def test_onlyoffice_extensions(filename, expected):
    assert get_viewer_type(filename) == expected


@pytest.mark.parametrize("filename,expected", [
    ("main.py",       ViewerType.CODE),
    ("index.ts",      ViewerType.CODE),
    ("Dockerfile",    ViewerType.UNSUPPORTED),  # no extension
    ("deploy.sh",     ViewerType.CODE),
    ("config.yaml",   ViewerType.CODE),
    ("config.toml",   ViewerType.CODE),
])
def test_code_extensions(filename, expected):
    assert get_viewer_type(filename) == expected


@pytest.mark.parametrize("filename,expected", [
    ("photo.jpg",    ViewerType.IMAGE),
    ("logo.png",     ViewerType.IMAGE),
    ("icon.svg",     ViewerType.IMAGE),
    ("banner.webp",  ViewerType.IMAGE),
])
def test_image_extensions(filename, expected):
    assert get_viewer_type(filename) == expected


@pytest.mark.parametrize("filename,expected", [
    ("archive.zip",   ViewerType.UNSUPPORTED),
    ("binary.exe",    ViewerType.UNSUPPORTED),
    ("noextension",   ViewerType.UNSUPPORTED),
    ("unknown.xyz",   ViewerType.UNSUPPORTED),
])
def test_unsupported_extensions(filename, expected):
    assert get_viewer_type(filename) == expected


def test_case_insensitive():
    assert get_viewer_type("REPORT.PDF") == ViewerType.ONLYOFFICE
    assert get_viewer_type("IMAGE.PNG") == ViewerType.IMAGE
    assert get_viewer_type("Script.PY") == ViewerType.CODE


# ─── Task 4 Integration Tests: /view and /vault-content endpoints ─────────────

import base64 as _b64
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.config import Settings, get_settings
from app.core.security import get_current_user_or_pod
from app.models.file_governance import GovernedFile, EncryptionState
import app.routers.secure_files as _sf_mod
from app.routers.secure_files import router as _sf_router


_VAULT_USER = {"sub": "VAULTUSER01", "role": "user", "name": "Vault User"}


def _vault_settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        debug=False,
        onlyoffice_jwt_secret="test-onlyoffice-jwt-secret-32-chars-min-xx",
        sms_gateway_url="http://fake-sms-gateway.test/send",
    )


class _FakeVaultService:
    """S3VaultService 스텁 — 설정된 bytes와 metadata를 반환한다."""

    def __init__(self, data: bytes, meta: dict | None = None):
        self._data = data
        self._meta = meta or {}

    def download_file(self, username=None, vault_id=None, encrypted_dek=None, file_id=None):
        return self._data, self._meta

    def list_user_files(self, username=None):
        return []


def _insert_gf(db_session, vault_id: str, filename: str) -> GovernedFile:
    """GovernedFile 레코드를 DB에 삽입하고 반환한다."""
    gf = GovernedFile(
        username="VAULTUSER01",
        filename=filename,
        file_path=f"vault/VAULTUSER01/{vault_id}/enc",
        file_type="application/octet-stream",
        file_size_bytes=128,
        classification="sensitive",
        classification_reason="test",
        status="active",
        ttl_days=7,
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        classified_at=datetime.now(timezone.utc),
        vault_id=vault_id,
        encrypted_dek=b"fake-dek",
        encryption_state=EncryptionState.ENCRYPTED.value,
    )
    db_session.add(gf)
    db_session.commit()
    db_session.refresh(gf)
    return gf


@pytest.fixture()
def sf_client(db_session):
    """secure_files router를 포함하는 최소 FastAPI TestClient."""
    _sf_app = FastAPI()
    _sf_app.include_router(_sf_router)

    def _override_db():
        yield db_session

    _sf_app.dependency_overrides[get_db] = _override_db
    _sf_app.dependency_overrides[get_settings] = _vault_settings
    _sf_app.dependency_overrides[get_current_user_or_pod] = lambda: _VAULT_USER.copy()

    with TestClient(_sf_app, raise_server_exceptions=False) as tc:
        yield tc

    _sf_app.dependency_overrides.clear()


# ── /view/{vault_id} ────────────────────────────────────────────────────────

def test_view_onlyoffice_returns_html(sf_client, db_session, monkeypatch):
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(b"pdfbytes"))
    _insert_gf(db_session, "v001", "report.pdf")
    resp = sf_client.get("/api/v1/secure/view/v001")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # OnlyOffice DocEditor 스크립트 또는 설정이 HTML에 포함되어야 한다
    body = resp.text.lower()
    assert "doceditor" in body or "onlyoffice" in body


def test_view_code_returns_pre_block(sf_client, db_session, monkeypatch):
    code = b"def hello():\n    return 'world'\n"
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(code))
    _insert_gf(db_session, "v002", "script.py")
    resp = sf_client.get("/api/v1/secure/view/v002")
    assert resp.status_code == 200
    assert "<pre>" in resp.text
    assert "hello" in resp.text


def test_view_code_escapes_html_chars(sf_client, db_session, monkeypatch):
    code = b"x = '<script>alert(1)</script>'\n"
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(code))
    _insert_gf(db_session, "v003", "exploit.py")
    resp = sf_client.get("/api/v1/secure/view/v003")
    assert resp.status_code == 200
    # 사용자 입력 <script>alert(1)은 &lt;로 이스케이프되어야 한다.
    # (DRM 컨텍스트 메뉴 방지용 <script> 태그는 HEAD에 정상적으로 존재)
    assert "<script>alert" not in resp.text
    assert "&lt;script&gt;" in resp.text


def test_view_image_returns_inline_img(sf_client, db_session, monkeypatch):
    # 최소 1×1 투명 PNG
    png_bytes = _b64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(png_bytes))
    _insert_gf(db_session, "v004", "photo.png")
    resp = sf_client.get("/api/v1/secure/view/v004")
    assert resp.status_code == 200
    assert "<img" in resp.text
    assert "base64," in resp.text
    assert 'oncontextmenu="return false;"' in resp.text


def test_view_unsupported_returns_403(sf_client, db_session, monkeypatch):
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(b"zipdata"))
    _insert_gf(db_session, "v005", "archive.zip")
    resp = sf_client.get("/api/v1/secure/view/v005")
    assert resp.status_code == 403


def test_view_missing_vault_returns_404(sf_client, db_session, monkeypatch):
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(b"x"))
    resp = sf_client.get("/api/v1/secure/view/nonexistent")
    assert resp.status_code == 404


def test_view_drm_headers_present(sf_client, db_session, monkeypatch):
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(b"data"))
    _insert_gf(db_session, "v006", "doc.docx")
    resp = sf_client.get("/api/v1/secure/view/v006")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")
    assert resp.headers.get("x-drm-protected") == "1"


# ── /vault-content/{vault_id} ────────────────────────────────────────────────

def test_vault_content_missing_token_returns_422(sf_client, db_session, monkeypatch):
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(b"data"))
    _insert_gf(db_session, "vc001", "report.pdf")
    resp = sf_client.get("/api/v1/secure/vault-content/vc001")
    assert resp.status_code == 422


def test_vault_content_invalid_token_returns_401(sf_client, db_session, monkeypatch):
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(b"data"))
    _insert_gf(db_session, "vc002", "report.pdf")
    resp = sf_client.get("/api/v1/secure/vault-content/vc002?token=badtoken")
    assert resp.status_code == 401


def test_vault_content_valid_token_returns_file_bytes(sf_client, db_session, monkeypatch):
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(b"PDFCONTENT"))
    _insert_gf(db_session, "vc003", "report.pdf")
    token = _sf_mod._create_vault_token("VAULTUSER01", "vc003")
    resp = sf_client.get(f"/api/v1/secure/vault-content/vc003?token={token}")
    assert resp.status_code == 200
    assert resp.content == b"PDFCONTENT"


def test_vault_content_drm_headers_present(sf_client, db_session, monkeypatch):
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(b"data"))
    _insert_gf(db_session, "vc004", "report.pdf")
    token = _sf_mod._create_vault_token("VAULTUSER01", "vc004")
    resp = sf_client.get(f"/api/v1/secure/vault-content/vc004?token={token}")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")
    assert resp.headers.get("x-drm-protected") == "1"


def test_vault_content_token_for_different_vault_returns_403(sf_client, db_session, monkeypatch):
    """다른 vault_id로 발급된 토큰은 403을 반환해야 한다 — 권한 우회 방어."""
    monkeypatch.setattr(_sf_mod, "_get_vault_service", lambda: _FakeVaultService(b"data"))
    _insert_gf(db_session, "vc-A", "a.pdf")
    _insert_gf(db_session, "vc-B", "b.pdf")
    token = _sf_mod._create_vault_token("VAULTUSER01", "vc-A")
    resp = sf_client.get(f"/api/v1/secure/vault-content/vc-B?token={token}")
    assert resp.status_code == 403
