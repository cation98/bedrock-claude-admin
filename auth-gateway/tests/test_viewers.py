"""OnlyOffice 뷰어 통합 API 테스트.

4 cases:
1. test_onlyoffice_config_endpoint       — 유효한 설정 구조 반환
2. test_onlyoffice_config_requires_auth  — 인증 없음 → 403
3. test_onlyoffice_download_disabled     — config 에 download=False 포함
4. test_callback_returns_error_0         — 콜백 엔드포인트 {"error": 0} 응답
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.core.security import get_current_user, get_current_user_or_pod
from app.routers import viewers as viewers_router

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# ---------------------------------------------------------------------------
# Minimal test infrastructure (SQLite in-memory)
# ---------------------------------------------------------------------------

_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(bind=_test_engine)


def _test_settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        onlyoffice_jwt_secret="",  # JWT 서명 비활성 (기본 테스트)
        onlyoffice_url="http://onlyoffice.claude-sessions.svc.cluster.local",
        debug=False,
    )


_DEFAULT_USER = {"sub": "TESTUSER01", "role": "user", "name": "Test User"}


def _mock_current_user() -> dict:
    return _DEFAULT_USER.copy()


def _build_test_app() -> FastAPI:
    app = FastAPI(title="Test Viewers")
    app.include_router(viewers_router.router)
    return app


_test_app = _build_test_app()


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=_test_engine)
    session = _TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture()
def client(db_session):
    """Authenticated test client — get_current_user_or_pod mocked."""

    def _override_db():
        try:
            yield db_session
        finally:
            pass

    _test_app.dependency_overrides[get_db] = _override_db
    _test_app.dependency_overrides[get_settings] = _test_settings
    _test_app.dependency_overrides[get_current_user_or_pod] = _mock_current_user
    _test_app.dependency_overrides[get_current_user] = _mock_current_user

    with TestClient(_test_app, raise_server_exceptions=False) as tc:
        yield tc

    _test_app.dependency_overrides.clear()


@pytest.fixture()
def unauthenticated_client(db_session):
    """Test client without auth override — returns real 403 on protected routes."""

    def _override_db():
        try:
            yield db_session
        finally:
            pass

    _test_app.dependency_overrides[get_db] = _override_db
    _test_app.dependency_overrides[get_settings] = _test_settings
    # get_current_user_or_pod은 오버라이드하지 않음 → 실제 인증 실패 → 403

    with TestClient(_test_app, raise_server_exceptions=False) as tc:
        yield tc

    _test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOnlyOfficeConfigEndpoint:
    """GET /api/v1/viewers/onlyoffice/config/{filename}"""

    def test_onlyoffice_config_endpoint(self, client):
        """1. 인증된 요청 → 유효한 OnlyOffice 설정 구조 반환."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/report.xlsx")

        assert resp.status_code == 200
        data = resp.json()

        # 최상위 키 검증
        assert "document" in data
        assert "editorConfig" in data

        # document 필드 검증
        doc = data["document"]
        assert doc["fileType"] == "xlsx"
        assert doc["title"] == "report.xlsx"
        assert "url" in doc
        assert "permissions" in doc

        # editorConfig 필드 검증
        editor = data["editorConfig"]
        assert "callbackUrl" in editor
        assert "user" in editor
        assert editor["user"]["id"] == "TESTUSER01"
        assert editor["lang"] == "ko"

    def test_onlyoffice_config_requires_auth(self, unauthenticated_client):
        """2. 인증 없음 → 403 Forbidden."""
        resp = unauthenticated_client.get(
            "/api/v1/viewers/onlyoffice/config/report.xlsx"
        )
        assert resp.status_code == 403

    def test_onlyoffice_download_disabled(self, client):
        """3. permissions.download 는 항상 False (보안 정책)."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/document.docx")

        assert resp.status_code == 200
        data = resp.json()
        permissions = data["document"]["permissions"]
        assert permissions["download"] is False

    def test_csp_header_set(self, client):
        """Content-Security-Policy: frame-ancestors 'self' 헤더 포함."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/slide.pptx")
        assert resp.status_code == 200
        assert "frame-ancestors" in resp.headers.get("content-security-policy", "")

    def test_file_type_extracted_from_extension(self, client):
        """파일 확장자를 fileType으로 올바르게 추출."""
        for filename, expected_ext in [
            ("data.csv", "csv"),
            ("report.docx", "docx"),
            ("presentation.pptx", "pptx"),
        ]:
            resp = client.get(f"/api/v1/viewers/onlyoffice/config/{filename}")
            assert resp.status_code == 200
            assert resp.json()["document"]["fileType"] == expected_ext

    def test_unsupported_extension_returns_400(self, client):
        """지원하지 않는 확장자 → 400 Bad Request."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/image.png")
        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["detail"]

    def test_document_type_mapping(self, client):
        """모든 확장자별 documentType 매핑 정확성."""
        cell_files = ["data.xlsx", "data.xls", "data.csv", "data.ods"]
        slide_files = ["deck.pptx", "deck.ppt", "deck.odp"]
        word_files = ["doc.docx", "doc.doc", "doc.odt", "doc.rtf"]

        for f in cell_files:
            resp = client.get(f"/api/v1/viewers/onlyoffice/config/{f}")
            assert resp.status_code == 200
            assert resp.json()["documentType"] == "cell", f"{f} should be cell"

        for f in slide_files:
            resp = client.get(f"/api/v1/viewers/onlyoffice/config/{f}")
            assert resp.status_code == 200
            assert resp.json()["documentType"] == "slide", f"{f} should be slide"

        for f in word_files:
            resp = client.get(f"/api/v1/viewers/onlyoffice/config/{f}")
            assert resp.status_code == 200
            assert resp.json()["documentType"] == "word", f"{f} should be word"

    def test_all_permissions_false(self, client):
        """보안 정책: download, edit, print, review 모두 False."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/report.xlsx")
        assert resp.status_code == 200
        perms = resp.json()["document"]["permissions"]
        assert perms["download"] is False
        assert perms["edit"] is False
        assert perms["print"] is False
        assert perms["review"] is False

    def test_no_token_when_jwt_secret_empty(self, client):
        """onlyoffice_jwt_secret 미설정 시 'token' 필드 없음."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/test.xlsx")
        assert resp.status_code == 200
        data = resp.json()
        # JWT secret이 비어있으면 token 필드가 없어야 함
        assert "token" not in data

    def test_token_present_when_jwt_secret_set(self, db_session):
        """onlyoffice_jwt_secret 설정 시 'token' 필드 포함."""

        def _settings_with_jwt() -> Settings:
            return Settings(
                database_url="sqlite://",
                jwt_secret_key="test-secret-key-256-bit-minimum-len",
                onlyoffice_jwt_secret="test-onlyoffice-secret-32chars!!!",
                debug=False,
            )

        def _override_db():
            try:
                yield db_session
            finally:
                pass

        _test_app.dependency_overrides[get_db] = _override_db
        _test_app.dependency_overrides[get_settings] = _settings_with_jwt
        _test_app.dependency_overrides[get_current_user_or_pod] = _mock_current_user

        with TestClient(_test_app, raise_server_exceptions=False) as tc:
            resp = tc.get("/api/v1/viewers/onlyoffice/config/test.xlsx")

        _test_app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert isinstance(data["token"], str)
        assert len(data["token"]) > 0

    def test_jwt_token_contains_config_claims(self, db_session):
        """JWT 토큰을 decode하면 config와 동일한 구조가 포함되어야 함."""
        from jose import jwt as jose_jwt

        jwt_secret = "test-onlyoffice-secret-32chars!!!"

        def _settings_with_jwt() -> Settings:
            return Settings(
                database_url="sqlite://",
                jwt_secret_key="test-secret-key-256-bit-minimum-len",
                onlyoffice_jwt_secret=jwt_secret,
                debug=False,
            )

        def _override_db():
            try:
                yield db_session
            finally:
                pass

        _test_app.dependency_overrides[get_db] = _override_db
        _test_app.dependency_overrides[get_settings] = _settings_with_jwt
        _test_app.dependency_overrides[get_current_user_or_pod] = _mock_current_user

        with TestClient(_test_app, raise_server_exceptions=False) as tc:
            resp = tc.get("/api/v1/viewers/onlyoffice/config/report.xlsx")

        _test_app.dependency_overrides.clear()

        data = resp.json()
        decoded = jose_jwt.decode(data["token"], jwt_secret, algorithms=["HS256"])
        assert decoded["document"]["fileType"] == "xlsx"
        assert decoded["document"]["title"] == "report.xlsx"
        assert decoded["document"]["permissions"]["download"] is False
        assert decoded["documentType"] == "cell"
        assert decoded["editorConfig"]["user"]["id"] == "TESTUSER01"


class TestFileTokenSystem:
    """파일 토큰 생성/소비 시스템 단위 테스트."""

    def test_create_and_consume_token(self):
        """토큰 생성 → 소비 → 정상 반환."""
        from app.routers.viewers import _create_file_token, _consume_file_token, _file_tokens
        _file_tokens.clear()

        token = _create_file_token("TESTUSER01", "report.xlsx")
        assert isinstance(token, str)
        assert len(token) > 0

        data = _consume_file_token(token)
        assert data is not None
        assert data["username"] == "TESTUSER01"
        assert data["file_path"] == "report.xlsx"

    def test_token_single_use(self):
        """토큰은 1회용 — 두 번째 소비는 None 반환."""
        from app.routers.viewers import _create_file_token, _consume_file_token, _file_tokens
        _file_tokens.clear()

        token = _create_file_token("TESTUSER01", "report.xlsx")
        first = _consume_file_token(token)
        assert first is not None

        second = _consume_file_token(token)
        assert second is None

    def test_expired_token_rejected(self):
        """만료된 토큰은 거부."""
        from app.routers.viewers import _consume_file_token, _file_tokens
        _file_tokens.clear()

        _file_tokens["expired-token"] = {
            "username": "TESTUSER01",
            "file_path": "old.xlsx",
            "expires": 0,  # 이미 만료
        }
        result = _consume_file_token("expired-token")
        assert result is None

    def test_invalid_token_rejected(self):
        """존재하지 않는 토큰은 None 반환."""
        from app.routers.viewers import _consume_file_token, _file_tokens
        _file_tokens.clear()

        result = _consume_file_token("nonexistent-token")
        assert result is None


class TestOnlyOfficeCallback:
    """POST /api/v1/viewers/onlyoffice/callback"""

    def test_callback_returns_error_0(self, client):
        """4. 콜백 요청 → {"error": 0} 응답."""
        resp = client.post(
            "/api/v1/viewers/onlyoffice/callback",
            json={"status": 1, "key": "doc-123"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

    def test_callback_save_ready_status(self, client):
        """status=2 (저장 준비) → {"error": 0} 응답."""
        resp = client.post(
            "/api/v1/viewers/onlyoffice/callback",
            json={
                "status": 2,
                "key": "doc-123",
                "url": "http://onlyoffice/internal/cache/doc-123.xlsx",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

    def test_callback_force_save_status(self, client):
        """status=6 (강제 저장) → {"error": 0} 응답."""
        resp = client.post(
            "/api/v1/viewers/onlyoffice/callback",
            json={
                "status": 6,
                "key": "doc-123",
                "url": "http://onlyoffice/internal/cache/doc-123.xlsx",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

    def test_callback_csp_header(self, client):
        """콜백 응답에도 Content-Security-Policy 헤더 포함."""
        resp = client.post(
            "/api/v1/viewers/onlyoffice/callback",
            json={"status": 0},
        )
        assert resp.status_code == 200
        assert "frame-ancestors" in resp.headers.get("content-security-policy", "")

    def test_callback_empty_body_accepted(self, client):
        """빈 JSON body → 정상 처리 (view-only 모드)."""
        resp = client.post(
            "/api/v1/viewers/onlyoffice/callback",
            json={},
        )
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

    def test_callback_no_jwt_secret_accepts_all(self, client):
        """onlyoffice_jwt_secret 미설정 시 모든 콜백 수락 (JWT 검증 건너뜀)."""
        resp = client.post(
            "/api/v1/viewers/onlyoffice/callback",
            json={"status": 2, "url": "http://example.com/doc.xlsx"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

    def test_callback_rejects_invalid_jwt(self, db_session):
        """JWT secret 설정 시 잘못된 토큰 → 403."""
        from jose import jwt as jose_jwt

        def _settings_with_jwt() -> Settings:
            return Settings(
                database_url="sqlite://",
                jwt_secret_key="test-secret-key-256-bit-minimum-len",
                onlyoffice_jwt_secret="test-onlyoffice-secret-32chars!!!",
                debug=False,
            )

        def _override_db():
            try:
                yield db_session
            finally:
                pass

        _test_app.dependency_overrides[get_db] = _override_db
        _test_app.dependency_overrides[get_settings] = _settings_with_jwt

        with TestClient(_test_app, raise_server_exceptions=False) as tc:
            resp = tc.post(
                "/api/v1/viewers/onlyoffice/callback",
                json={"status": 1},
                headers={"Authorization": "Bearer invalid-token"},
            )

        _test_app.dependency_overrides.clear()
        assert resp.status_code == 403

    def test_callback_accepts_valid_jwt(self, db_session):
        """JWT secret 설정 시 올바른 토큰 → 200."""
        from jose import jwt as jose_jwt

        jwt_secret = "test-onlyoffice-secret-32chars!!!"

        def _settings_with_jwt() -> Settings:
            return Settings(
                database_url="sqlite://",
                jwt_secret_key="test-secret-key-256-bit-minimum-len",
                onlyoffice_jwt_secret=jwt_secret,
                debug=False,
            )

        def _override_db():
            try:
                yield db_session
            finally:
                pass

        _test_app.dependency_overrides[get_db] = _override_db
        _test_app.dependency_overrides[get_settings] = _settings_with_jwt

        valid_token = jose_jwt.encode({"status": 1}, jwt_secret, algorithm="HS256")

        with TestClient(_test_app, raise_server_exceptions=False) as tc:
            resp = tc.post(
                "/api/v1/viewers/onlyoffice/callback",
                json={"status": 1},
                headers={"Authorization": f"Bearer {valid_token}"},
            )

        _test_app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}
