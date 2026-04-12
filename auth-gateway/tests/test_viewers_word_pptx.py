"""Word/PPTX 재현 테스트 skeleton.

목적:
  - test_viewers.py 의 xlsx 편중 커버리지 gap 을 메우는 최소 재현 케이스.
  - 프로덕션 코드 수정 없이 실패 재현 → 수정 후 green 전환 패턴.

현재 상태:
  - 대부분 pytest.skip 으로 마킹. 환경 구성 후 skip 제거.
  - 일부는 mock 만으로 즉시 실행 가능 (isolated unit tests).

관련 이슈:
  - P2-BUG1: OnlyOffice callback envelope 포맷 복원
  - P2-BUG2: localhost URL → cluster DNS rewrite
  - P2-BUG3: kubectl subprocess → Python k8s client stream
  - I6: Word/PPTX save 흐름 미검증 (이 파일이 gap 을 커버)
"""

import json
import re
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.core.security import get_current_user, get_current_user_or_pod
from app.routers import viewers as viewers_router
from app.models.edit_session import EditSession
from app.models.file_share import FileShareACL, SharedDataset
from app.models.user import User


# ---------------------------------------------------------------------------
# 공용 테스트 인프라 (test_viewers.py 와 동일 패턴 — import 하지 않고 복사)
# ---------------------------------------------------------------------------

_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(bind=_test_engine)

_DEFAULT_JWT_SECRET = "test-onlyoffice-jwt-secret-32-chars-min-xx"
_DEFAULT_USER = {"sub": "TESTUSER01", "role": "user", "name": "Test User"}


def _test_settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        onlyoffice_jwt_secret=_DEFAULT_JWT_SECRET,
        onlyoffice_url="http://onlyoffice.claude-sessions.svc.cluster.local",
        debug=False,
    )


def _mock_current_user() -> dict:
    return _DEFAULT_USER.copy()


def _build_test_app() -> FastAPI:
    app = FastAPI(title="Test Viewers Word/PPTX")
    app.include_router(viewers_router.router)
    return app


_test_app = _build_test_app()


@pytest.fixture()
def db_session(monkeypatch):
    Base.metadata.create_all(bind=_test_engine)
    monkeypatch.setattr(viewers_router, "SessionLocal", _TestSessionLocal)
    session = _TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture()
def client(db_session):
    def _override_db():
        try:
            yield db_session
        finally:
            pass

    async def _override_viewer_user() -> dict:
        return _DEFAULT_USER.copy()

    _test_app.dependency_overrides[get_db] = _override_db
    _test_app.dependency_overrides[get_settings] = _test_settings
    _test_app.dependency_overrides[get_current_user_or_pod] = _mock_current_user
    _test_app.dependency_overrides[get_current_user] = _mock_current_user
    _test_app.dependency_overrides[viewers_router._get_viewer_user] = _override_viewer_user

    with TestClient(_test_app, raise_server_exceptions=False) as tc:
        yield tc

    _test_app.dependency_overrides.clear()


def _post_callback(client, payload: dict):
    """JWT 필수 콜백 헬퍼 (P2-F1 패턴)."""
    from jose import jwt as jose_jwt
    signed = {**payload, "exp": int(time.time()) + 60}
    token = jose_jwt.encode(signed, _DEFAULT_JWT_SECRET, algorithm="HS256")
    return client.post(
        "/api/v1/viewers/onlyoffice/callback",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _mk_edit_session(
    db,
    *,
    username: str = "TESTUSER01",
    file_path: str = "document.docx",
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


def _extract_config_from_html(html: str) -> dict:
    m = re.search(r"var config = (\{.*?\});\s*config\.type", html, re.DOTALL)
    assert m, f"config JSON을 HTML에서 찾지 못함: {html[:200]!r}"
    return json.loads(m.group(1))


# ---------------------------------------------------------------------------
# [A] _onlyoffice_doc_type 단위 테스트 (즉시 실행 가능 — mock 불필요)
# ---------------------------------------------------------------------------

class TestOnlyOfficeDocTypeUnit:
    """_onlyoffice_doc_type 순수 단위 테스트 — HTTP 불필요."""

    def test_docx_returns_word(self):
        assert viewers_router._onlyoffice_doc_type(".docx") == "word"

    def test_doc_returns_word(self):
        assert viewers_router._onlyoffice_doc_type(".doc") == "word"

    def test_odt_returns_word(self):
        assert viewers_router._onlyoffice_doc_type(".odt") == "word"

    def test_rtf_returns_word(self):
        assert viewers_router._onlyoffice_doc_type(".rtf") == "word"

    def test_pptx_returns_slide(self):
        assert viewers_router._onlyoffice_doc_type(".pptx") == "slide"

    def test_ppt_returns_slide(self):
        assert viewers_router._onlyoffice_doc_type(".ppt") == "slide"

    def test_odp_returns_slide(self):
        assert viewers_router._onlyoffice_doc_type(".odp") == "slide"

    def test_xlsx_returns_cell(self):
        assert viewers_router._onlyoffice_doc_type(".xlsx") == "cell"

    def test_unknown_ext_falls_through_to_word(self):
        """알 수 없는 확장자 → default 'word' (현재 동작)."""
        assert viewers_router._onlyoffice_doc_type(".unknown") == "word"


# ---------------------------------------------------------------------------
# [B] _build_onlyoffice_config — docx/pptx 구조 검증 (HTTP 엔드포인트 통해)
# ---------------------------------------------------------------------------

class TestBuildConfigWordPptx:
    """Config API가 docx/pptx 에 대해 올바른 구조를 반환하는지 검증."""

    def test_docx_config_document_type_is_word(self, client):
        resp = client.get("/api/v1/viewers/onlyoffice/config/report.docx")
        assert resp.status_code == 200
        assert resp.json()["documentType"] == "word"

    def test_pptx_config_document_type_is_slide(self, client):
        resp = client.get("/api/v1/viewers/onlyoffice/config/deck.pptx")
        assert resp.status_code == 200
        assert resp.json()["documentType"] == "slide"

    def test_docx_config_file_type_field(self, client):
        resp = client.get("/api/v1/viewers/onlyoffice/config/report.docx")
        assert resp.status_code == 200
        assert resp.json()["document"]["fileType"] == "docx"

    def test_pptx_config_file_type_field(self, client):
        resp = client.get("/api/v1/viewers/onlyoffice/config/deck.pptx")
        assert resp.status_code == 200
        assert resp.json()["document"]["fileType"] == "pptx"

    def test_docx_permissions_all_false_in_view_mode(self, client):
        """config API (view-only) — docx permissions 전체 False."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/report.docx")
        assert resp.status_code == 200
        perms = resp.json()["document"]["permissions"]
        assert perms["download"] is False
        assert perms["edit"] is False
        assert perms["print"] is False
        assert perms["review"] is False

    def test_pptx_permissions_all_false_in_view_mode(self, client):
        """config API (view-only) — pptx permissions 전체 False."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/deck.pptx")
        assert resp.status_code == 200
        perms = resp.json()["document"]["permissions"]
        assert perms["download"] is False
        assert perms["edit"] is False

    def test_docx_jwt_token_present(self, client):
        """JWT secret 설정 시 docx config에도 token 필드 포함."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/report.docx")
        assert resp.status_code == 200
        assert "token" in resp.json(), "docx config에 JWT token 없음"

    def test_pptx_jwt_token_present(self, client):
        """JWT secret 설정 시 pptx config에도 token 필드 포함."""
        resp = client.get("/api/v1/viewers/onlyoffice/config/deck.pptx")
        assert resp.status_code == 200
        assert "token" in resp.json(), "pptx config에 JWT token 없음"

    def test_docx_edit_mode_permissions_edit_true(self, client, db_session):
        """docx /edit 엔드포인트 → permissions.edit=True (EDITABLE_EXTENSIONS 포함)."""
        resp = client.get("/api/v1/viewers/onlyoffice/edit/TESTUSER01/report.docx")
        assert resp.status_code == 200
        cfg = _extract_config_from_html(resp.text)
        assert cfg["document"]["permissions"]["edit"] is True

    def test_pptx_edit_mode_permissions_edit_true(self, client, db_session):
        """pptx /edit 엔드포인트 → permissions.edit=True."""
        resp = client.get("/api/v1/viewers/onlyoffice/edit/TESTUSER01/deck.pptx")
        assert resp.status_code == 200
        cfg = _extract_config_from_html(resp.text)
        assert cfg["document"]["permissions"]["edit"] is True


# ---------------------------------------------------------------------------
# [C] _save_edited_file — Word/PPTX 콜백 저장 흐름 (핵심 gap)
# ---------------------------------------------------------------------------

class TestSaveFlowWordPptx:
    """_save_edited_file 이 docx/pptx 세션에서도 올바르게 호출되는지 검증.

    gap: test_viewers.py 의 TestCallbackSaveFlow 는 xlsx 만 사용.
    이 class 가 docx/pptx 동등성을 확인한다.
    """

    def test_callback_status2_docx_calls_save_edited_file(
        self, client, db_session, monkeypatch
    ):
        """status=2 + docx session → _save_edited_file 호출됨."""
        session = _mk_edit_session(db_session, file_path="docs/report.docx")
        calls: list[tuple] = []

        async def _fake_save(sess, url, filetype):
            calls.append((sess.document_key, url, filetype))

        monkeypatch.setattr(viewers_router, "_save_edited_file", _fake_save)

        resp = _post_callback(client, {
            "status": 2,
            "key": session.document_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/cache/r.docx",
            "filetype": "docx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}
        assert len(calls) == 1, "_save_edited_file 가 호출되지 않음 (docx)"
        assert calls[0][2] == "docx"

    def test_callback_status2_pptx_calls_save_edited_file(
        self, client, db_session, monkeypatch
    ):
        """status=2 + pptx session → _save_edited_file 호출됨."""
        session = _mk_edit_session(db_session, file_path="slides/deck.pptx")
        calls: list[tuple] = []

        async def _fake_save(sess, url, filetype):
            calls.append((sess.document_key, url, filetype))

        monkeypatch.setattr(viewers_router, "_save_edited_file", _fake_save)

        resp = _post_callback(client, {
            "status": 2,
            "key": session.document_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/cache/deck.pptx",
            "filetype": "pptx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}
        assert len(calls) == 1, "_save_edited_file 가 호출되지 않음 (pptx)"
        assert calls[0][2] == "pptx"

    def test_callback_status2_docx_deletes_session(
        self, client, db_session, monkeypatch
    ):
        """docx 저장 성공 → EditSession 행 DELETE (P2-BUG1 검증)."""
        session = _mk_edit_session(db_session, file_path="report.docx", version=2)
        session_id = session.id

        async def _noop_save(sess, url, filetype):
            return None

        monkeypatch.setattr(viewers_router, "_save_edited_file", _noop_save)

        resp = _post_callback(client, {
            "status": 2,
            "key": session.document_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/x.docx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}

        db_session.expire_all()
        assert db_session.query(EditSession).filter_by(id=session_id).first() is None, \
            "docx 저장 후 EditSession 행이 삭제되지 않음"

    def test_callback_status2_pptx_deletes_session(
        self, client, db_session, monkeypatch
    ):
        """pptx 저장 성공 → EditSession 행 DELETE."""
        session = _mk_edit_session(db_session, file_path="deck.pptx", version=1)
        session_id = session.id

        async def _noop_save(sess, url, filetype):
            return None

        monkeypatch.setattr(viewers_router, "_save_edited_file", _noop_save)

        resp = _post_callback(client, {
            "status": 2,
            "key": session.document_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/x.pptx",
        })
        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.query(EditSession).filter_by(id=session_id).first() is None, \
            "pptx 저장 후 EditSession 행이 삭제되지 않음"

    def test_callback_status2_docx_save_failure_marks_error(
        self, client, db_session, monkeypatch
    ):
        """docx 저장 실패(RuntimeError) → session.status='save_failed'."""
        session = _mk_edit_session(db_session, file_path="broken.docx")

        async def _fail_save(sess, url, filetype):
            raise RuntimeError("kubectl cp failed: pod not found")

        monkeypatch.setattr(viewers_router, "_save_edited_file", _fail_save)

        resp = _post_callback(client, {
            "status": 2,
            "key": session.document_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/x.docx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 1}

        db_session.expire_all()
        updated = db_session.query(EditSession).filter_by(id=session.id).one()
        assert updated.status == "save_failed"
        assert updated.last_error is not None

    def test_callback_status6_forcesave_docx(
        self, client, db_session, monkeypatch
    ):
        """status=6(force-save) + docx → _save_edited_file 호출, session 유지 (P2-iter3 #7)."""
        session = _mk_edit_session(db_session, file_path="force.docx", version=1)
        original_key = session.document_key
        calls: list[tuple] = []

        async def _noop_save(sess, url, filetype):
            calls.append((sess.document_key, url, filetype))

        monkeypatch.setattr(viewers_router, "_save_edited_file", _noop_save)

        resp = _post_callback(client, {
            "status": 6,
            "key": original_key,
            "url": "http://documentserver.claude-sessions.svc.cluster.local/f.docx",
        })
        assert resp.status_code == 200
        assert resp.json() == {"error": 0}
        assert len(calls) == 1, "status=6(force-save) 시 _save_edited_file 미호출 (docx)"

        # force-save 후 세션이 남아 있어야 함 (status=2와 달리 DELETE 하지 않음)
        db_session.expire_all()
        still_exists = db_session.query(EditSession).filter_by(id=session.id).first()
        assert still_exists is not None, "force-save 후 EditSession 이 삭제됨 (docx)"


# ---------------------------------------------------------------------------
# [D] localhost URL rewrite — P2-BUG2 검증 (docx/pptx)
# ---------------------------------------------------------------------------

class TestLocalhostRewriteWordPptx:
    """_save_edited_file 의 localhost→cluster DNS rewrite 가 docx/pptx 에서도 동작하는지.

    P2-BUG2: OO DS가 callback url을 http://localhost/cache/files/... 로 보냄.
    BUG2 패치는 테스트(test_viewers.py:TestSaveRewriteLocalhostToClusterDNS)에서
    xlsx 파일명으로만 검증됨 — docx/pptx 동등성 확인 필요.
    """

    @pytest.mark.skip(reason="httpx mock 필요 — 환경 구성 후 skip 제거")
    def test_localhost_url_rewritten_docx(self, db_session, monkeypatch):
        """docx 세션 콜백 url=http://localhost/... → cluster DNS로 rewrite 되어 httpx 호출."""
        import app.routers.viewers as _v

        captured_urls: list[str] = []

        # httpx.AsyncClient.stream mock
        # (실제 환경에서는 pytest-httpx 또는 respx 활용 권장)
        raise NotImplementedError("httpx mock 구성 필요")

    @pytest.mark.skip(reason="httpx mock 필요 — 환경 구성 후 skip 제거")
    def test_localhost_url_rewritten_pptx(self, db_session, monkeypatch):
        """pptx 세션 콜백 url=http://localhost/... → cluster DNS rewrite."""
        raise NotImplementedError("httpx mock 구성 필요")


# ---------------------------------------------------------------------------
# [E] container_path 계산 검증 — docx/pptx 경로
# ---------------------------------------------------------------------------

class TestContainerPathWordPptx:
    """_save_edited_file 의 container_path 계산이 docx/pptx 에서 올바른지.

    현재 _save_edited_file 은 session.file_path 를 그대로 사용하므로
    확장자에 무관하게 동작해야 함. 하지만 테스트는 없음.
    """

    @pytest.mark.skip(reason="K8sService mock 필요 — 환경 구성 후 skip 제거")
    def test_docx_container_path_personal(self, db_session, monkeypatch):
        """개인 docx 파일 → /home/node/workspace/{file_path} 로 저장."""
        # 예: file_path="docs/report.docx" → /home/node/workspace/docs/report.docx
        raise NotImplementedError("K8sService mock 구성 필요")

    @pytest.mark.skip(reason="K8sService mock 필요 — 환경 구성 후 skip 제거")
    def test_pptx_container_path_personal(self, db_session, monkeypatch):
        """개인 pptx 파일 → /home/node/workspace/{file_path} 로 저장."""
        raise NotImplementedError("K8sService mock 구성 필요")

    @pytest.mark.skip(reason="K8sService mock 필요 — 환경 구성 후 skip 제거")
    def test_docx_container_path_shared(self, db_session, monkeypatch):
        """공유 docx 파일 → /home/node/workspace/shared-data/{file_path}."""
        raise NotImplementedError("K8sService mock 구성 필요")


# ---------------------------------------------------------------------------
# [F] 파일 토큰 다중 fetch 지원 — H1(P2-BUG4) 회귀 방어
# ---------------------------------------------------------------------------

class TestFileTokenMultiFetch:
    """OnlyOffice Document Server는 Word/PPTX 원본을 변환 파이프라인에서 다중 fetch한다.
    2026-04-12 로그로 확증됨 (.docx: 3회 요청, .xlsx: 1회 요청).

    따라서 `_consume_file_token` 은 1회용 소비가 아니라 TTL 기반 재검증으로 동작해야 한다.
    이 테스트 클래스는 H1 fix의 회귀 방어용.
    """

    @pytest.fixture(autouse=True)
    def _isolate_memory_tokens(self, monkeypatch):
        """테스트 간 _file_tokens 오염 방지 + Redis 강제 우회(memory fallback)."""
        viewers_router._file_tokens.clear()
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: None)
        yield
        viewers_router._file_tokens.clear()

    def test_token_reusable_on_three_fetches_within_ttl(self):
        """OO DS가 .docx/.pptx 를 3회 fetch 해도 모두 성공해야 한다 (H1 fix 조건)."""
        token = viewers_router._create_file_token("USER01", "docs/report.docx", ttl_seconds=300)

        first = viewers_router._consume_file_token(token)
        assert first is not None and first["file_path"] == "docs/report.docx", \
            "1차 fetch 실패 — 토큰 생성/저장 경로 문제"

        # conversion daemon 2차 fetch
        second = viewers_router._consume_file_token(token)
        assert second is not None, \
            "2차 fetch 시 토큰 무효 — OO DS 다중 fetch 중 401 (H1 버그)"

        # editor 3차 fetch (retry)
        third = viewers_router._consume_file_token(token)
        assert third is not None, \
            "3차 fetch 시 토큰 무효 — 재시도 실패 (H1 버그)"

        # 데이터 무결성: 3회 모두 동일 payload
        assert first == second == third

    def test_token_rejected_after_ttl_expiry(self):
        """TTL 만료 후엔 토큰이 무효화되어야 한다 (보안 요건 유지)."""
        token = viewers_router._create_file_token("USER01", "docs/report.docx", ttl_seconds=1)
        assert viewers_router._consume_file_token(token) is not None

        time.sleep(1.1)
        assert viewers_router._consume_file_token(token) is None, \
            "TTL 만료 후에도 토큰이 유효함 — GC 경로 누락"

    def test_token_username_path_binding_preserved(self):
        """재사용 가능해져도 (username, path) 바인딩은 유지되어야 한다."""
        token = viewers_router._create_file_token("USER01", "a.docx", ttl_seconds=300)
        for _ in range(3):
            data = viewers_router._consume_file_token(token)
            assert data is not None
            assert data["username"] == "USER01"
            assert data["file_path"] == "a.docx"

    def test_unknown_token_returns_none(self):
        """존재하지 않는 토큰은 재사용 허용 전후 동일하게 None."""
        assert viewers_router._consume_file_token("nonexistent-token-xyz") is None
