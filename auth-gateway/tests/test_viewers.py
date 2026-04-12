"""OnlyOffice 뷰어 통합 API 테스트.

기존 18 cases:
1-11. Config endpoint (구조, 인증, 권한, 확장자, documentType, JWT)
12-15. File token 시스템 (create/consume/expire/invalid)
16-22. Callback (error 0, save, force-save, CSP, empty, JWT 검증)

T9 추가 22 cases:
23. test_callback_status_2_downloads_and_saves
24. test_callback_status_2_updates_db_version
25. test_callback_status_2_kubectl_cp_failure
26. test_edit_mode_permissions_true
27. test_edit_mode_mode_is_edit
28. test_edit_mode_forcesave_enabled
29. test_shared_mode_chat_enabled
30. test_shared_endpoint_allows_owner
31. test_shared_endpoint_allows_acl_user
32. test_shared_endpoint_allows_acl_team
33. test_shared_endpoint_denies_non_acl_403
34. test_personal_file_second_user_view_only
35. test_personal_file_owner_always_edit
36. test_personal_key_format
37. test_shared_key_format
38. test_key_rotation_on_save
39. test_view_edit_share_same_key
40. test_callback_status_4_cleanup
41. test_callback_status_6_force_save
42. test_callback_status_3_error_state
43. test_callback_status_10_technical_error
44. test_file_proxy_streaming_not_buffered
"""

import inspect
import json
import re

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

# 테이블 정의를 등록하기 위해 모델 import (Base.metadata에 포함되도록)
from app.models.edit_session import EditSession
from app.models.file_share import FileShareACL, SharedDataset
from app.models.user import User


# ---------------------------------------------------------------------------
# Minimal test infrastructure (SQLite in-memory)
# ---------------------------------------------------------------------------

_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(bind=_test_engine)


_DEFAULT_TEST_JWT_SECRET = "test-onlyoffice-jwt-secret-32-chars-min-xx"


def _test_settings() -> Settings:
    # P2 #1 이후 onlyoffice_jwt_secret은 필수. 테스트 기본값으로 32자 dummy 사용.
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        onlyoffice_jwt_secret=_DEFAULT_TEST_JWT_SECRET,
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
def db_session(monkeypatch):
    """SQLite in-memory DB 세션.

    콜백 핸들러가 `SessionLocal()`을 직접 호출하므로(FOR UPDATE + 수동 커밋을 위해
    Depends(get_db)를 우회), SessionLocal을 테스트 엔진 바인딩으로 monkeypatch 한다.
    이 덕분에 콜백 테스트에서도 같은 in-memory DB를 공유한다.
    """
    Base.metadata.create_all(bind=_test_engine)
    monkeypatch.setattr(viewers_router, "SessionLocal", _TestSessionLocal)
    session = _TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=_test_engine)


def _override_viewer_user(user: dict | None = None):
    """_get_viewer_user 의존성을 테스트용으로 override하는 콜러블을 반환."""
    payload = user or _DEFAULT_USER

    async def _impl() -> dict:
        return payload.copy()

    return _impl


@pytest.fixture()
def client(db_session):
    """Authenticated test client — 주요 인증 의존성 전부 mocked."""

    def _override_db():
        try:
            yield db_session
        finally:
            pass

    _test_app.dependency_overrides[get_db] = _override_db
    _test_app.dependency_overrides[get_settings] = _test_settings
    _test_app.dependency_overrides[get_current_user_or_pod] = _mock_current_user
    _test_app.dependency_overrides[get_current_user] = _mock_current_user
    _test_app.dependency_overrides[viewers_router._get_viewer_user] = _override_viewer_user()

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

    with TestClient(_test_app, raise_server_exceptions=False) as tc:
        yield tc

    _test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers for T9 tests
# ---------------------------------------------------------------------------

def _post_callback(client, payload: dict):
    """콜백은 JWT 필수(P2 #1). payload를 서명된 Bearer 토큰으로 감싸 호출."""
    from jose import jwt as jose_jwt
    token = jose_jwt.encode(payload, _DEFAULT_TEST_JWT_SECRET, algorithm="HS256")
    return client.post(
        "/api/v1/viewers/onlyoffice/callback",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _extract_config_from_html(html: str) -> dict:
    """`_render_onlyoffice_html()` 이 만든 HTML에서 config JSON을 추출."""
    m = re.search(r"var config = (\{.*?\});\s*config\.type", html, re.DOTALL)
    assert m, f"config JSON을 HTML에서 찾지 못함: {html[:200]!r}"
    return json.loads(m.group(1))


def _mk_edit_session(
    db,
    *,
    username: str = "TESTUSER01",
    file_path: str = "report.xlsx",
    is_shared: bool = False,
    mount_id: int | None = None,
    version: int = 1,
    status: str = "editing",
) -> EditSession:
    if is_shared:
        key = viewers_router._doc_key_shared(mount_id, file_path, version)
    else:
        key = viewers_router._doc_key_personal(username, file_path, version)
    row = EditSession(
        document_key=key,
        file_path=file_path,
        owner_username=username.upper(),
        is_shared=is_shared,
        mount_id=mount_id,
        status=status,
        version=version,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _mk_shared_dataset(db, *, owner: str, name: str = "team-docs") -> SharedDataset:
    ds = SharedDataset(
        owner_username=owner,
        dataset_name=name,
        file_path=f"shared-data/{name}",
        file_type="xlsx",
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


def _mk_acl(
    db,
    *,
    dataset_id: int,
    share_type: str,
    share_target: str,
    granted_by: str = "ADMIN01",
    revoked: bool = False,
) -> FileShareACL:
    from datetime import datetime, timezone
    acl = FileShareACL(
        dataset_id=dataset_id,
        share_type=share_type,
        share_target=share_target,
        granted_by=granted_by,
        revoked_at=datetime.now(timezone.utc) if revoked else None,
    )
    db.add(acl)
    db.commit()
    db.refresh(acl)
    return acl


def _mk_user(db, *, username: str, team_name: str | None = None) -> User:
    u = User(username=username.upper(), name=username, team_name=team_name)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ---------------------------------------------------------------------------
# Tests — 기존 18 cases (config, token, callback)
# ---------------------------------------------------------------------------

class TestOnlyOfficeConfigEndpoint:
    """GET /api/v1/viewers/onlyoffice/config/{filename}"""

    def test_onlyoffice_config_endpoint(self, client):
        """1. 인증된 요청 → 유효한 OnlyOffice 설정 구조 반환."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/report.xlsx")

        assert resp.status_code == 200
        data = resp.json()

        assert "document" in data
        assert "editorConfig" in data

        doc = data["document"]
        assert doc["fileType"] == "xlsx"
        assert doc["title"] == "report.xlsx"
        assert "url" in doc
        assert "permissions" in doc

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
        """3. permissions.download 는 config API 기본(view-only)에서 False."""
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
        """config API(view-only): download/edit/print/review 모두 False."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/report.xlsx")
        assert resp.status_code == 200
        perms = resp.json()["document"]["permissions"]
        assert perms["download"] is False
        assert perms["edit"] is False
        assert perms["print"] is False
        assert perms["review"] is False

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
        from app.routers.viewers import _create_file_token, _consume_file_token, _file_tokens
        _file_tokens.clear()

        token = _create_file_token("TESTUSER01", "report.xlsx")
        first = _consume_file_token(token)
        assert first is not None

        second = _consume_file_token(token)
        assert second is None

    def test_expired_token_rejected(self):
        from app.routers.viewers import _consume_file_token, _file_tokens
        _file_tokens.clear()

        _file_tokens["expired-token"] = {
            "username": "TESTUSER01",
            "file_path": "old.xlsx",
            "expires": 0,
        }
        result = _consume_file_token("expired-token")
        assert result is None

    def test_invalid_token_rejected(self):
        from app.routers.viewers import _consume_file_token, _file_tokens
        _file_tokens.clear()

        result = _consume_file_token("nonexistent-token")
        assert result is None


class TestOnlyOfficeCallback:
    """POST /api/v1/viewers/onlyoffice/callback"""

    def test_callback_returns_error_0(self, client):
        """콜백 요청 (unknown key) → {"error": 0} 응답."""
        resp = _post_callback(client, {"status": 1, "key": "doc-123"})
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

    def test_callback_save_ready_status(self, client):
        """status=2 (저장 준비) without matching session → {"error": 0}."""
        resp = _post_callback(client, {
            "status": 2,
            "key": "doc-123",
            "url": "http://documentserver.claude-sessions.svc.cluster.local/cache/x.xlsx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

    def test_callback_force_save_status(self, client):
        """status=6 without matching session → {"error": 0} (unknown key 처리)."""
        resp = _post_callback(client, {
            "status": 6,
            "key": "doc-123",
            "url": "http://documentserver.claude-sessions.svc.cluster.local/cache/x.xlsx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

    def test_callback_csp_header(self, client):
        """콜백 응답은 순수 JSON — CSP frame-ancestors 헤더가 포함되지 않아야 한다.

        설계(T2) 기준: callback은 S2S 엔드포인트이므로 HTML embedding 관련 헤더는 부적절.
        iframe 응답이 아니라 Document Server가 직접 호출하는 JSON 응답이다.
        """
        resp = _post_callback(client, {"status": 0})
        assert resp.status_code == 200
        assert "frame-ancestors" not in resp.headers.get("content-security-policy", "")

    def test_callback_empty_body_accepted(self, client):
        """빈 JSON body → 정상 처리 (view-only 모드)."""
        resp = _post_callback(client, {})
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

    def test_callback_rejects_missing_jwt(self, client):
        """JWT 필수 — 토큰 없이 요청하면 403 (P2 #1)."""
        resp = client.post(
            "/api/v1/viewers/onlyoffice/callback",
            json={"status": 2, "url": "http://example.com/doc.xlsx"},
        )
        assert resp.status_code == 403

    def test_callback_rejects_invalid_jwt(self, db_session):
        """JWT secret 설정 시 잘못된 토큰 → 403."""

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


# ---------------------------------------------------------------------------
# T9 추가: OnlyOffice 편집 모드 22개 테스트
# ---------------------------------------------------------------------------

class TestCallbackSaveFlow:
    """Priority 1: status=2 저장 흐름 (Document Server → auth-gateway → Pod)."""

    def test_callback_status_2_downloads_and_saves(self, client, db_session, monkeypatch):
        """status=2 + valid session + download URL → _save_edited_file 호출됨."""
        session = _mk_edit_session(db_session, file_path="docs/report.xlsx")
        calls: list[tuple] = []

        async def _fake_save(sess, url, filetype):
            calls.append((sess.document_key, url, filetype))

        monkeypatch.setattr(viewers_router, "_save_edited_file", _fake_save)

        resp = _post_callback(client, {
            "status": 2,
            "key": session.document_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/cache/r.xlsx",
            "filetype": "xlsx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}
        assert len(calls) == 1
        assert calls[0][0] == session.document_key
        assert calls[0][2] == "xlsx"

    def test_callback_status_2_updates_db_version(self, client, db_session, monkeypatch):
        """status=2 저장 성공 → session.status='saved', version++."""
        session = _mk_edit_session(db_session, file_path="report.xlsx", version=3)
        original_key = session.document_key
        original_version = session.version

        async def _noop_save(sess, url, filetype):
            return None

        monkeypatch.setattr(viewers_router, "_save_edited_file", _noop_save)

        resp = _post_callback(client, {
            "status": 2,
            "key": original_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/x.xlsx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

        db_session.expire_all()
        updated = db_session.query(EditSession).filter_by(id=session.id).one()
        assert updated.status == "saved"
        assert updated.version == original_version + 1

    def test_callback_status_2_kubectl_cp_failure(self, client, db_session, monkeypatch):
        """저장 중 kubectl cp 실패 → session.status='save_failed', last_error 기록."""
        session = _mk_edit_session(db_session, file_path="big.xlsx")

        async def _fail_save(sess, url, filetype):
            raise RuntimeError("kubectl cp failed: pod not found")

        monkeypatch.setattr(viewers_router, "_save_edited_file", _fail_save)

        resp = _post_callback(client, {
            "status": 2,
            "key": session.document_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/x.xlsx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 1}

        db_session.expire_all()
        updated = db_session.query(EditSession).filter_by(id=session.id).one()
        assert updated.status == "save_failed"
        assert updated.last_error is not None
        assert "kubectl cp failed" in updated.last_error


class TestEditModeConfig:
    """Priority 2: /edit /shared 엔드포인트의 편집 모드 config."""

    def test_edit_mode_permissions_true(self, client, db_session):
        """첫 사용자가 /edit/{username}/{file} 열면 permissions.edit=True."""
        resp = client.get("/api/v1/viewers/onlyoffice/edit/TESTUSER01/plan.xlsx")
        assert resp.status_code == 200
        cfg = _extract_config_from_html(resp.text)
        assert cfg["document"]["permissions"]["edit"] is True
        assert cfg["document"]["permissions"]["download"] is True

    def test_edit_mode_mode_is_edit(self, client, db_session):
        """첫 사용자 /edit → editorConfig.mode == 'edit'."""
        resp = client.get("/api/v1/viewers/onlyoffice/edit/TESTUSER01/memo.docx")
        assert resp.status_code == 200
        cfg = _extract_config_from_html(resp.text)
        assert cfg["editorConfig"]["mode"] == "edit"

    def test_edit_mode_forcesave_enabled(self, client, db_session):
        """편집 모드에서 customization.forcesave=True (Ctrl+S 지원)."""
        resp = client.get("/api/v1/viewers/onlyoffice/edit/TESTUSER01/deck.pptx")
        assert resp.status_code == 200
        cfg = _extract_config_from_html(resp.text)
        assert cfg["editorConfig"]["customization"]["forcesave"] is True

    def test_shared_mode_chat_enabled(self, client, db_session):
        """/shared/{mount_id}/{file} 편집 모드 → customization.chat=True (co-editing)."""
        ds = _mk_shared_dataset(db_session, owner="TESTUSER01", name="team-plans")

        resp = client.get(f"/api/v1/viewers/onlyoffice/shared/{ds.id}/quarterly.xlsx")
        assert resp.status_code == 200
        cfg = _extract_config_from_html(resp.text)
        assert cfg["editorConfig"]["customization"]["chat"] is True
        assert cfg["editorConfig"]["customization"]["comments"] is True


class TestSharedEndpointACL:
    """Priority 3: /shared 엔드포인트 ACL 검증."""

    def test_shared_endpoint_allows_owner(self, client, db_session):
        """dataset.owner_username == 요청자 → 통과 (ACL 없이)."""
        ds = _mk_shared_dataset(db_session, owner="TESTUSER01")
        resp = client.get(f"/api/v1/viewers/onlyoffice/shared/{ds.id}/doc.xlsx")
        assert resp.status_code == 200

    def test_shared_endpoint_allows_acl_user(self, client, db_session):
        """share_type=user + target=요청자 사번 → 통과."""
        ds = _mk_shared_dataset(db_session, owner="OWNER01")  # 다른 소유자
        _mk_acl(db_session, dataset_id=ds.id, share_type="user", share_target="TESTUSER01")

        resp = client.get(f"/api/v1/viewers/onlyoffice/shared/{ds.id}/report.xlsx")
        assert resp.status_code == 200

    def test_shared_endpoint_allows_acl_team(self, client, db_session):
        """share_type=team + target=요청자 team_name → 통과."""
        _mk_user(db_session, username="TESTUSER01", team_name="AT/DT개발팀")
        ds = _mk_shared_dataset(db_session, owner="OWNER02")
        _mk_acl(db_session, dataset_id=ds.id, share_type="team", share_target="AT/DT개발팀")

        resp = client.get(f"/api/v1/viewers/onlyoffice/shared/{ds.id}/spec.xlsx")
        assert resp.status_code == 200

    def test_shared_endpoint_denies_non_acl_403(self, client, db_session):
        """다른 소유자 + 매칭 ACL 없음 → 403."""
        _mk_user(db_session, username="TESTUSER01", team_name="보안팀")
        ds = _mk_shared_dataset(db_session, owner="OWNER03")
        # 다른 사용자/팀에 대한 ACL만 있음
        _mk_acl(db_session, dataset_id=ds.id, share_type="user", share_target="OTHER99")
        _mk_acl(db_session, dataset_id=ds.id, share_type="team", share_target="영업팀")
        # 회수된 ACL은 무시되어야 함
        _mk_acl(
            db_session,
            dataset_id=ds.id,
            share_type="user",
            share_target="TESTUSER01",
            revoked=True,
        )

        resp = client.get(f"/api/v1/viewers/onlyoffice/shared/{ds.id}/secret.xlsx")
        assert resp.status_code == 403


class TestPersonalFileEditLock:
    """Priority 4: 개인 파일 편집 잠금."""

    def test_personal_file_second_user_view_only(self, client, db_session):
        """기존 편집 세션이 있을 때 두 번째 사용자가 열면 view-only."""
        # 기존 세션이 이미 editing 중
        _mk_edit_session(db_session, username="TESTUSER01", file_path="shared.xlsx")

        # TESTUSER01 본인이 다시 열어도 '두 번째 사용자'로 취급 (보수적 잠금)
        resp = client.get("/api/v1/viewers/onlyoffice/edit/TESTUSER01/shared.xlsx")
        assert resp.status_code == 200
        cfg = _extract_config_from_html(resp.text)
        assert cfg["editorConfig"]["mode"] == "view"
        assert cfg["document"]["permissions"]["edit"] is False

    def test_personal_file_owner_always_edit(self, client, db_session):
        """세션이 없으면 소유자가 첫 진입자로서 edit 모드."""
        resp = client.get("/api/v1/viewers/onlyoffice/edit/TESTUSER01/new.xlsx")
        assert resp.status_code == 200
        cfg = _extract_config_from_html(resp.text)
        assert cfg["editorConfig"]["mode"] == "edit"
        assert cfg["document"]["permissions"]["edit"] is True


class TestKeyGeneration:
    """Priority 5: document key 생성/로테이션."""

    def test_personal_key_format(self):
        """개인 key = sha256(personal:USERNAME_UPPER:path:version) 앞 20자."""
        import hashlib
        key = viewers_router._doc_key_personal("testuser01", "docs/report.xlsx", 7)
        expected = hashlib.sha256(
            b"personal:TESTUSER01:docs/report.xlsx:7"
        ).hexdigest()[:20]
        assert key == expected
        assert len(key) == 20

    def test_shared_key_format(self):
        """공유 key = sha256(shared:mount_id:path:version) 앞 20자."""
        import hashlib
        key = viewers_router._doc_key_shared(42, "quarterly.xlsx", 2)
        expected = hashlib.sha256(b"shared:42:quarterly.xlsx:2").hexdigest()[:20]
        assert key == expected
        assert len(key) == 20

    def test_key_rotation_on_save(self, client, db_session, monkeypatch):
        """status=2 저장 후 version++ → 같은 파일 재진입 시 새 key 생성 가능."""
        v1_key = viewers_router._doc_key_personal("TESTUSER01", "rot.xlsx", 1)
        v2_key = viewers_router._doc_key_personal("TESTUSER01", "rot.xlsx", 2)
        assert v1_key != v2_key

        session = _mk_edit_session(db_session, file_path="rot.xlsx", version=1)
        assert session.document_key == v1_key

        async def _noop_save(sess, url, filetype):
            return None

        monkeypatch.setattr(viewers_router, "_save_edited_file", _noop_save)

        _post_callback(client, {
            "status": 2,
            "key": v1_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/rot.xlsx",
        })

        db_session.expire_all()
        updated = db_session.query(EditSession).filter_by(id=session.id).one()
        # 기존 세션은 saved + version 증가. saved 상태이므로 다음 열기 때는 새 세션이 생성됨.
        assert updated.version == 2
        assert updated.status == "saved"
        # 로테이션된 key가 v2_key와 일치할 수 있어야 함 (다음 세션 생성 규칙)
        next_key = viewers_router._doc_key_personal(
            updated.owner_username, updated.file_path, updated.version
        )
        assert next_key == v2_key

    def test_view_edit_share_same_key(self, client, db_session):
        """활성 편집 세션이 있으면 /onlyoffice/{user}/{path} view 진입은 같은 key를 사용."""
        session = _mk_edit_session(db_session, username="TESTUSER01", file_path="live.xlsx")

        view_resp = client.get("/api/v1/viewers/onlyoffice/TESTUSER01/live.xlsx")
        edit_resp = client.get("/api/v1/viewers/onlyoffice/edit/TESTUSER01/live.xlsx")
        assert view_resp.status_code == 200
        assert edit_resp.status_code == 200

        view_cfg = _extract_config_from_html(view_resp.text)
        edit_cfg = _extract_config_from_html(edit_resp.text)

        assert view_cfg["document"]["key"] == session.document_key
        assert edit_cfg["document"]["key"] == session.document_key
        # view와 edit 이 다른 mode지만 동일 키를 공유
        assert view_cfg["editorConfig"]["mode"] == "view"


class TestCallbackOtherStatuses:
    """Priority 6: 콜백의 나머지 status 처리."""

    def test_callback_status_4_cleanup(self, client, db_session):
        """status=4 (변경 없이 닫힘) → session.status='saved'."""
        session = _mk_edit_session(db_session, file_path="nochange.xlsx")

        resp = _post_callback(client, {"status": 4, "key": session.document_key})
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

        db_session.expire_all()
        updated = db_session.query(EditSession).filter_by(id=session.id).one()
        assert updated.status == "saved"

    def test_callback_status_6_force_save(self, client, db_session, monkeypatch):
        """status=6 (force-save) → 저장 후 status='editing' 유지 + version 동일(P2 #7).

        P2 #7: force-save 중 version을 증가시키면 Document Server가 사용하는
        document_key와 어긋나 다음 저장이 unknown-key로 실패한다. 편집 세션은
        그대로 유지하고 파일만 덮어쓴다.
        """
        session = _mk_edit_session(db_session, file_path="force.xlsx", version=1)
        original_version = session.version
        original_key = session.document_key

        async def _noop_save(sess, url, filetype):
            return None

        monkeypatch.setattr(viewers_router, "_save_edited_file", _noop_save)

        resp = _post_callback(client, {
            "status": 6,
            "key": original_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/f.xlsx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

        db_session.expire_all()
        updated = db_session.query(EditSession).filter_by(id=session.id).one()
        assert updated.status == "editing"             # force-save는 편집 계속
        assert updated.version == original_version     # version은 유지 (P2 #7)
        assert updated.document_key == original_key    # key 로테이션 안 함

    def test_callback_status_3_error_state(self, client, db_session):
        """status=3 (저장 에러) → session.status='error' + last_error."""
        session = _mk_edit_session(db_session, file_path="broken.xlsx")

        resp = _post_callback(client, {
            "status": 3,
            "key": session.document_key,
            "error": "conversion failure",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

        db_session.expire_all()
        updated = db_session.query(EditSession).filter_by(id=session.id).one()
        assert updated.status == "error"
        assert updated.last_error == "conversion failure"

    def test_callback_status_10_technical_error(self, client, db_session):
        """status=10 (기술적 오류, v8.2+) → session.status='error'."""
        session = _mk_edit_session(db_session, file_path="tech.xlsx")

        resp = _post_callback(client, {
            "status": 10,
            "key": session.document_key,
            "error": "internal engine crash",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

        db_session.expire_all()
        updated = db_session.query(EditSession).filter_by(id=session.id).one()
        assert updated.status == "error"
        assert updated.last_error == "internal engine crash"


class TestFileProxyStreaming:
    """Priority 7: 파일 프록시 스트리밍 (버퍼링 없이 청크 중계)."""

    def test_file_proxy_streaming_not_buffered(self):
        """stream_file이 httpx.stream + aiter_bytes(chunk_size=65536)로 동작해야 한다.

        T3 설계: `iter([resp.content])` 같은 전체 메모리 버퍼링이 아니라
        `httpx.send(stream=True)` + `aiter_bytes(chunk_size=65536)`로
        50MB 파일도 64KB 청크로 점진 전송해야 한다.
        """
        src = inspect.getsource(viewers_router.stream_file)

        # 안티패턴: iter([resp.content]) 또는 resp.content 전체를 리스트에 감싸는 형태
        assert "iter([resp.content])" not in src, (
            "stream_file은 전체 바디를 메모리에 버퍼링하면 안 된다 (T3 회귀)"
        )
        # 올바른 스트리밍 API 사용 여부
        assert "stream=True" in src or "http.stream(" in src, (
            "httpx stream 모드를 사용해야 한다"
        )
        assert "aiter_bytes(chunk_size=65536)" in src, (
            "64KB 청크 단위로 aiter_bytes를 호출해야 한다 (메모리 상한 유지)"
        )
        # StreamingResponse로 래핑되어야 함
        assert "StreamingResponse" in src
