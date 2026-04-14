# DRM Phase 1: 뷰어 통제 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모든 파일을 서비스 뷰어(OnlyOffice/Code Viewer/Image)로만 열람하게 하고, 클라이언트에 원본 바이트 전달을 완전히 차단한다.

**Architecture:** `secure_files.py`의 직접 파일 바이트 응답(`secure_get`)을 뷰어 리다이렉션으로 교체하고, `viewers.py`의 OnlyOffice 설정에 DRM 모드(다운로드/인쇄/복사 금지)를 추가한다. 터미널 Pod의 외부 파일 반출은 K8s NetworkPolicy egress 화이트리스트로 차단한다.

**Tech Stack:** FastAPI, Python 3.12, kubernetes-python, pikepdf (이미지 Exif 제거: Pillow), pytest

**중요 파일 경로 사전 파악:**
- `auth-gateway/app/routers/viewers.py` — OnlyOffice/파일 뷰어 (기존)
- `auth-gateway/app/routers/secure_files.py` — S3 Vault 업로드/다운로드 (기존)
- `auth-gateway/app/services/s3_vault.py` — S3 서비스 (기존)
- `infra/k8s/network-policy-tightened.yaml` — 터미널 Pod NetworkPolicy (초안, 미적용)
- `auth-gateway/tests/` — 기존 테스트 디렉토리

---

## 파일 변경 맵

| 파일 | 작업 | 목적 |
|------|------|------|
| `auth-gateway/app/routers/viewers.py` | 수정 | `_build_onlyoffice_config`에 `drm_mode` 파라미터 추가, 보안 헤더 helper 추가 |
| `auth-gateway/app/routers/secure_files.py` | 수정 | `secure_get` → 뷰어 리다이렉션으로 교체, `/view/{vault_id}` 엔드포인트 신설 |
| `auth-gateway/app/services/file_viewer_router.py` | 신규 | 파일 확장자 → 뷰어 유형 라우팅 로직 |
| `infra/k8s/network-policy-drm.yaml` | 신규 | 터미널 Pod egress 화이트리스트 NetworkPolicy |
| `auth-gateway/tests/test_drm_viewer.py` | 신규 | DRM 뷰어 엔드포인트 테스트 |
| `auth-gateway/tests/test_drm_headers.py` | 신규 | 보안 헤더 강제 적용 테스트 |

---

## Task 1: 파일 뷰어 라우터 서비스 추가

**파일:**
- 신규: `auth-gateway/app/services/file_viewer_router.py`
- 신규: `auth-gateway/tests/test_drm_viewer.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# auth-gateway/tests/test_drm_viewer.py
import pytest
from app.services.file_viewer_router import ViewerType, get_viewer_type

def test_office_documents_route_to_onlyoffice():
    assert get_viewer_type("report.pdf") == ViewerType.ONLYOFFICE
    assert get_viewer_type("data.xlsx") == ViewerType.ONLYOFFICE
    assert get_viewer_type("doc.docx") == ViewerType.ONLYOFFICE
    assert get_viewer_type("slides.pptx") == ViewerType.ONLYOFFICE

def test_code_files_route_to_code_viewer():
    assert get_viewer_type("main.py") == ViewerType.CODE
    assert get_viewer_type("app.js") == ViewerType.CODE
    assert get_viewer_type("config.yaml") == ViewerType.CODE
    assert get_viewer_type("deploy.sh") == ViewerType.CODE

def test_images_route_to_image_viewer():
    assert get_viewer_type("photo.jpg") == ViewerType.IMAGE
    assert get_viewer_type("icon.png") == ViewerType.IMAGE
    assert get_viewer_type("diagram.svg") == ViewerType.IMAGE

def test_unknown_binary_returns_unsupported():
    assert get_viewer_type("binary.exe") == ViewerType.UNSUPPORTED
    assert get_viewer_type("archive.zip") == ViewerType.UNSUPPORTED
    assert get_viewer_type("noextension") == ViewerType.UNSUPPORTED
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd auth-gateway
pytest tests/test_drm_viewer.py -v
# Expected: FAIL — ImportError: cannot import name 'ViewerType'
```

- [ ] **Step 3: 파일 뷰어 라우터 서비스 구현**

```python
# auth-gateway/app/services/file_viewer_router.py
"""파일 확장자 → 뷰어 유형 라우팅.

각 파일 유형에 적합한 뷰어를 결정한다.
"""
from enum import Enum

class ViewerType(str, Enum):
    ONLYOFFICE  = "onlyoffice"   # PDF, Office 문서
    CODE        = "code"          # 소스코드, 설정 파일
    IMAGE       = "image"         # 이미지
    UNSUPPORTED = "unsupported"   # 열람 불가 바이너리

_ONLYOFFICE_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".odt", ".ods", ".odp",
    ".txt", ".csv", ".rtf",
}

_CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".sh", ".bash", ".zsh", ".fish",
    ".yaml", ".yml", ".toml", ".json", ".env",
    ".go", ".rs", ".java", ".kt", ".swift",
    ".c", ".cpp", ".h", ".hpp", ".cs",
    ".rb", ".php", ".r", ".sql", ".md",
    ".html", ".css", ".scss", ".vue",
    ".tf", ".hcl", ".Dockerfile",
}

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".bmp", ".ico"}


def get_viewer_type(filename: str) -> ViewerType:
    """파일명(확장자 포함)으로 뷰어 유형 결정."""
    import os
    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        return ViewerType.UNSUPPORTED
    if ext in _ONLYOFFICE_EXTS:
        return ViewerType.ONLYOFFICE
    if ext in _CODE_EXTS:
        return ViewerType.CODE
    if ext in _IMAGE_EXTS:
        return ViewerType.IMAGE
    return ViewerType.UNSUPPORTED
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_drm_viewer.py -v
# Expected: 4 passed
```

- [ ] **Step 5: 커밋**

```bash
git add auth-gateway/app/services/file_viewer_router.py auth-gateway/tests/test_drm_viewer.py
git commit -m "feat(drm): 파일 확장자 → 뷰어 유형 라우팅 서비스 추가"
```

---

## Task 2: OnlyOffice DRM 모드 — 다운로드/인쇄/복사 강제 비활성화

**파일:**
- 수정: `auth-gateway/app/routers/viewers.py` (함수 `_build_onlyoffice_config`)
- 신규: `auth-gateway/tests/test_drm_headers.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# auth-gateway/tests/test_drm_headers.py
import pytest
from app.routers.viewers import _build_onlyoffice_config

class _FakeSettings:
    onlyoffice_jwt_secret = None

def test_drm_mode_disables_all_permissions():
    """DRM 모드에서 다운로드/인쇄/복사가 모두 False인지 확인."""
    config = _build_onlyoffice_config(
        filename="secret.xlsx",
        username="N1102359",
        display_name="홍길동",
        settings=_FakeSettings(),
        drm_mode=True,
        editable=False,
    )
    perms = config["document"]["permissions"]
    assert perms["download"] is False
    assert perms["print"] is False
    assert perms["copy"] is False
    assert perms["edit"] is False

def test_non_drm_mode_respects_editable_true():
    """DRM 모드가 아닐 때 editable=True면 download 허용."""
    config = _build_onlyoffice_config(
        filename="draft.docx",
        username="N1102359",
        display_name="홍길동",
        settings=_FakeSettings(),
        drm_mode=False,
        editable=True,
        file_download_url="http://internal/file.docx",
    )
    perms = config["document"]["permissions"]
    assert perms["download"] is True

def test_drm_mode_disables_help_and_chat():
    """DRM 모드에서 OnlyOffice 우측 메뉴, 채팅, 도움말 비활성화."""
    config = _build_onlyoffice_config(
        filename="report.pdf",
        username="N1102359",
        display_name="홍길동",
        settings=_FakeSettings(),
        drm_mode=True,
        editable=False,
    )
    custom = config["editorConfig"]["customization"]
    assert custom.get("chat") is False
    assert custom.get("help") is False
    assert custom.get("hideRightMenu") is True
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_drm_headers.py -v
# Expected: FAIL — TypeError: _build_onlyoffice_config() got unexpected keyword argument 'drm_mode'
```

- [ ] **Step 3: `_build_onlyoffice_config`에 `drm_mode` 파라미터 추가**

`auth-gateway/app/routers/viewers.py`의 `_build_onlyoffice_config` 함수 시그니처와 `permissions` 딕셔너리를 수정한다. 기존 파라미터 목록 끝에 `drm_mode: bool = False` 추가:

```python
def _build_onlyoffice_config(
    filename: str,
    username: str,
    display_name: str,
    settings: Settings,
    *,
    editable: bool = False,
    shared: bool = False,
    document_key: str | None = None,
    file_download_url: str | None = None,
    file_owner_username: str | None = None,
    drm_mode: bool = False,          # ← 신규: True이면 모든 내보내기 차단
) -> dict:
```

`permissions` 딕셔너리 부분을 아래로 교체 (기존 `permissions = { "download": editable, ...}` 블록):

```python
    if drm_mode:
        # DRM 모드: 모든 내보내기/복사/인쇄 차단
        permissions = {
            "download": False,
            "edit": False,
            "print": False,
            "review": False,
            "comment": False,
            "copy": False,
            "fillForms": False,
            "modifyFilter": False,
            "modifyContentControl": False,
        }
    else:
        permissions = {
            "download": editable,
            "edit": editable,
            "print": editable,
            "review": editable,
            "comment": editable,
            "fillForms": editable,
            "modifyFilter": editable,
            "modifyContentControl": editable,
        }
```

`customization` 딕셔너리 끝에 DRM 전용 설정 추가:

```python
    customization = {
        "toolbarNoTabs": not editable,
        "compactHeader": not editable,
        "hideRightMenu": not editable or drm_mode,   # ← drm_mode 조건 추가
        "chat": bool(shared and editable and not drm_mode),
        "comments": bool(editable and not drm_mode),
        "forcesave": bool(editable),
        "help": not drm_mode,                        # ← drm_mode에서 도움말 숨김
    }
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_drm_headers.py -v
# Expected: 3 passed
```

- [ ] **Step 5: 기존 테스트 회귀 확인**

```bash
pytest tests/ -v --ignore=tests/test_drm_viewer.py -x -q
# Expected: 기존 테스트 모두 통과 (새 파라미터 기본값 drm_mode=False이므로 기존 동작 유지)
```

- [ ] **Step 6: 커밋**

```bash
git add auth-gateway/app/routers/viewers.py auth-gateway/tests/test_drm_headers.py
git commit -m "feat(drm): OnlyOffice DRM 모드 — drm_mode=True 시 다운로드/인쇄/복사 전면 차단"
```

---

## Task 3: 보안 HTTP 응답 헤더 helper 추가

**파일:**
- 수정: `auth-gateway/app/routers/secure_files.py`

- [ ] **Step 1: 실패하는 테스트 추가** (기존 `test_drm_headers.py`에 append)

```python
# auth-gateway/tests/test_drm_headers.py 에 추가
from app.routers.secure_files import _drm_response_headers

def test_drm_response_headers_present():
    headers = _drm_response_headers()
    assert headers["Content-Disposition"] == "inline"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in headers["Cache-Control"]
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert "X-Frame-Options" in headers
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_drm_headers.py::test_drm_response_headers_present -v
# Expected: FAIL — ImportError
```

- [ ] **Step 3: `secure_files.py` 상단에 helper 추가**

```python
# auth-gateway/app/routers/secure_files.py — 기존 import 블록 아래에 추가

def _drm_response_headers() -> dict[str, str]:
    """파일 응답에 강제 적용할 DRM 보안 헤더."""
    return {
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "Content-Security-Policy": "default-src 'self'; object-src 'none'",
        "X-Frame-Options": "SAMEORIGIN",
    }
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_drm_headers.py -v
# Expected: 4 passed
```

- [ ] **Step 5: 커밋**

```bash
git add auth-gateway/app/routers/secure_files.py auth-gateway/tests/test_drm_headers.py
git commit -m "feat(drm): DRM 보안 HTTP 헤더 helper 추가 (Content-Disposition: inline 등)"
```

---

## Task 4: S3 Vault 파일 뷰어 엔드포인트 신설

**파일:**
- 수정: `auth-gateway/app/routers/secure_files.py`
- 수정: `auth-gateway/tests/test_drm_viewer.py`

- [ ] **Step 1: 실패하는 테스트 추가**

```python
# auth-gateway/tests/test_drm_viewer.py 에 추가

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def _auth_header(username: str = "N1102359") -> dict:
    """테스트용 JWT 헤더 생성 (conftest.py의 패턴 재사용)."""
    from tests.conftest import make_token
    return {"Authorization": f"Bearer {make_token(username)}"}

def test_vault_view_office_file_returns_html(monkeypatch):
    """S3 Vault Office 파일 → HTML 뷰어 응답 반환."""
    mock_vault = MagicMock()
    mock_vault.download_file.return_value = (b"fake-xlsx-bytes", {
        "original-filename": "report.xlsx",
        "owner": "N1102359",
    })
    monkeypatch.setattr("app.routers.secure_files._get_vault_service", lambda: mock_vault)

    resp = client.get("/api/v1/secure/view/abc123", headers=_auth_header())
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "onlyoffice" in resp.text.lower() or "doceditor" in resp.text.lower()

def test_vault_view_python_file_returns_html_code_viewer(monkeypatch):
    """S3 Vault .py 파일 → Code Viewer HTML 응답 반환."""
    mock_vault = MagicMock()
    mock_vault.download_file.return_value = (b"print('hello')", {
        "original-filename": "script.py",
        "owner": "N1102359",
    })
    monkeypatch.setattr("app.routers.secure_files._get_vault_service", lambda: mock_vault)

    resp = client.get("/api/v1/secure/view/def456", headers=_auth_header())
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]

def test_vault_view_unsupported_returns_403(monkeypatch):
    """S3 Vault .exe 파일 → 열람 불가 응답."""
    mock_vault = MagicMock()
    mock_vault.download_file.return_value = (b"\x4d\x5a", {
        "original-filename": "setup.exe",
        "owner": "N1102359",
    })
    monkeypatch.setattr("app.routers.secure_files._get_vault_service", lambda: mock_vault)

    resp = client.get("/api/v1/secure/view/ghi789", headers=_auth_header())
    assert resp.status_code == 403

def test_vault_view_enforces_drm_headers(monkeypatch):
    """뷰어 응답에 DRM 보안 헤더 포함 확인."""
    mock_vault = MagicMock()
    mock_vault.download_file.return_value = (b"dummy", {
        "original-filename": "note.txt",
        "owner": "N1102359",
    })
    monkeypatch.setattr("app.routers.secure_files._get_vault_service", lambda: mock_vault)

    resp = client.get("/api/v1/secure/view/jkl000", headers=_auth_header())
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert "no-store" in resp.headers.get("Cache-Control", "")
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_drm_viewer.py::test_vault_view_office_file_returns_html -v
# Expected: FAIL — 404 Not Found (엔드포인트 미존재)
```

- [ ] **Step 3: `/view/{vault_id}` 엔드포인트 구현**

`auth-gateway/app/routers/secure_files.py`에 `secure_list` 엔드포인트 위에 추가:

```python
from app.services.file_viewer_router import ViewerType, get_viewer_type

@router.get("/view/{vault_id}")
async def secure_view(
    vault_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    vault_svc: S3VaultService = Depends(_get_vault_service),
    settings: Settings = Depends(get_settings),
):
    """S3 Vault 파일을 DRM 뷰어로 제공 (원본 바이트 클라이언트 전달 금지).

    - Office 문서 → OnlyOffice HTML (drm_mode=True)
    - 소스코드   → Monaco Code Viewer HTML
    - 이미지     → Exif 제거 후 인라인 img 태그 HTML
    - 바이너리   → 403 열람 불가
    """
    from fastapi.responses import HTMLResponse
    from app.routers.viewers import _build_onlyoffice_config, _render_onlyoffice_html

    username = current_user["sub"]

    try:
        file_bytes, metadata = vault_svc.download_file(username, vault_id)
    except Exception as exc:
        logger.error("Vault view error for %s/%s: %s", username, vault_id, exc)
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")

    original_filename = metadata.get("original-filename", "file")
    viewer_type = get_viewer_type(original_filename)
    drm_headers = _drm_response_headers()

    if viewer_type == ViewerType.UNSUPPORTED:
        raise HTTPException(
            status_code=403,
            detail=f"'{original_filename}' 파일 형식은 서비스 내 열람이 지원되지 않습니다.",
        )

    if viewer_type == ViewerType.ONLYOFFICE:
        # 파일을 임시 토큰으로 내부 URL에 캐싱 후 OnlyOffice에 전달
        import tempfile, os, base64
        # OnlyOffice는 서버에서 파일을 fetch해야 하므로 임시 인메모리 토큰 발급
        from app.routers.viewers import _create_file_token, _file_tokens
        tmp_token = _create_file_token(username, original_filename, ttl_seconds=300)
        _file_tokens[tmp_token]["_vault_bytes"] = base64.b64encode(file_bytes).decode()

        display_name = current_user.get("name") or username
        config = _build_onlyoffice_config(
            filename=original_filename,
            username=username,
            display_name=display_name,
            settings=settings,
            drm_mode=True,
            editable=False,
            file_download_url=(
                f"http://auth-gateway.platform.svc.cluster.local"
                f"/api/v1/viewers/file/{username}/{original_filename}?token={tmp_token}"
            ),
        )
        html = _render_onlyoffice_html(original_filename, config)
        return HTMLResponse(content=html, headers=drm_headers)

    if viewer_type == ViewerType.CODE:
        import html as html_mod
        ext = os.path.splitext(original_filename)[1].lstrip(".")
        try:
            code_text = file_bytes.decode("utf-8", errors="replace")
        except Exception:
            code_text = "[바이너리 파일 — 텍스트 열람 불가]"
        escaped = html_mod.escape(code_text)
        html_content = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{html_mod.escape(original_filename)}</title>
<style>
  body {{ margin: 0; background: #1e1e1e; color: #d4d4d4; font-family: monospace; }}
  pre {{ padding: 16px; overflow: auto; white-space: pre-wrap; word-break: break-all; }}
  .header {{ background: #252526; padding: 8px 16px; font-size: 12px; color: #858585;
             border-bottom: 1px solid #3c3c3c; display: flex; justify-content: space-between; }}
  .badge {{ background: #007acc; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; }}
</style>
</head><body>
<div class="header">
  <span>{html_mod.escape(original_filename)}</span>
  <span class="badge">읽기 전용 (DRM 보호)</span>
</div>
<pre>{escaped}</pre>
</body></html>"""
        return HTMLResponse(content=html_content, headers=drm_headers)

    if viewer_type == ViewerType.IMAGE:
        # Exif 제거 후 base64 인라인 이미지로 제공
        import base64, io, os as _os
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(file_bytes))
            clean_buf = io.BytesIO()
            img.save(clean_buf, format=img.format or "PNG")
            clean_bytes = clean_buf.getvalue()
        except Exception:
            clean_bytes = file_bytes  # PIL 없으면 원본 사용
        ext = _os.path.splitext(original_filename)[1].lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp"}.get(ext, "image/png")
        b64 = base64.b64encode(clean_bytes).decode()
        import html as html_mod
        html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{html_mod.escape(original_filename)}</title>
<style>
  body {{ margin: 0; background: #1a1a1a; display: flex; flex-direction: column;
          align-items: center; min-height: 100vh; }}
  .header {{ width: 100%; background: #252526; padding: 8px 16px; font-size: 12px;
             color: #858585; font-family: monospace; box-sizing: border-box;
             display: flex; justify-content: space-between; }}
  .badge {{ background: #007acc; color: white; padding: 2px 8px; border-radius: 3px; }}
  img {{ max-width: 100%; max-height: calc(100vh - 40px); object-fit: contain;
         margin-top: 16px; }}
</style>
</head><body>
<div class="header">
  <span>{html_mod.escape(original_filename)}</span>
  <span class="badge">읽기 전용 (DRM 보호)</span>
</div>
<img src="data:{mime};base64,{b64}" alt="{html_mod.escape(original_filename)}"
     oncontextmenu="return false;" draggable="false">
</body></html>"""
        return HTMLResponse(content=html_content, headers=drm_headers)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_drm_viewer.py -v
# Expected: 모든 테스트 통과
```

- [ ] **Step 5: 커밋**

```bash
git add auth-gateway/app/routers/secure_files.py \
        auth-gateway/app/services/file_viewer_router.py \
        auth-gateway/tests/test_drm_viewer.py
git commit -m "feat(drm): S3 Vault 파일 뷰어 엔드포인트 추가 GET /api/v1/secure/view/{vault_id}"
```

---

## Task 5: `secure_get` 직접 다운로드 차단

**파일:**
- 수정: `auth-gateway/app/routers/secure_files.py` (`secure_get` 함수)

`secure_get` (`POST /api/v1/secure/get`)은 현재 원본 바이트를 직접 반환한다. 이를 뷰어 URL 리다이렉션 응답으로 교체한다.

- [ ] **Step 1: 실패하는 테스트 추가**

```python
# auth-gateway/tests/test_drm_viewer.py 에 추가

def test_secure_get_blocks_direct_download():
    """POST /api/v1/secure/get 는 403을 반환하고 뷰어 URL을 안내해야 한다."""
    resp = client.post(
        "/api/v1/secure/get",
        json={"vault_id": "anyid"},
        headers=_auth_header(),
    )
    assert resp.status_code == 403
    body = resp.json()
    assert "view" in body.get("detail", "").lower() or "viewer" in body.get("viewer_url", "").lower()
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_drm_viewer.py::test_secure_get_blocks_direct_download -v
# Expected: FAIL — 기존 secure_get은 200과 파일 바이트를 반환함
```

- [ ] **Step 3: `secure_get` 함수 교체**

`auth-gateway/app/routers/secure_files.py`의 `secure_get` 함수 본문을 아래로 교체한다 (함수 시그니처는 유지):

```python
@router.post("/get")
async def secure_get(
    body: SecureGetRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """S3 Vault 직접 다운로드는 DRM 정책에 의해 차단됨.

    원본 파일 열람은 /api/v1/secure/view/{vault_id} 뷰어를 사용하세요.
    반출이 필요한 경우 반출 승인 요청(/api/v1/secure/export/request)을 사용하세요.
    """
    raise HTTPException(
        status_code=403,
        detail="직접 파일 다운로드는 DRM 정책에 의해 차단됩니다. "
               f"뷰어 URL: /api/v1/secure/view/{body.vault_id}",
    )
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_drm_viewer.py -v
# Expected: 모든 테스트 통과
```

- [ ] **Step 5: 커밋**

```bash
git add auth-gateway/app/routers/secure_files.py auth-gateway/tests/test_drm_viewer.py
git commit -m "feat(drm): secure_get 직접 다운로드 차단 — 뷰어 URL 안내로 교체"
```

---

## Task 6: 터미널 Pod NetworkPolicy — egress 화이트리스트 적용

**파일:**
- 신규: `infra/k8s/network-policy-drm.yaml`
- 수정: 기존 `infra/k8s/network-policy-tightened.yaml` (참고용 → 이 태스크에서 대체)

> **주의**: 이 Task는 인프라 변경으로 K8s 클러스터에 직접 영향을 준다. 적용 전 반드시 테스트 Pod 1개로 카나리 검증 후 전체 적용.

- [ ] **Step 1: NetworkPolicy YAML 작성**

```yaml
# infra/k8s/network-policy-drm.yaml
# DRM Phase 1: 터미널 Pod 파일 반출 경로 차단
# 허용: 개발 도구(npm/pip/github), 서비스 내부(Bedrock/RDS/Redis), OnlyOffice
# 차단: 그 외 임의 외부 서버로의 파일 전송
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: drm-terminal-pod-egress
  namespace: claude-sessions
  labels:
    phase: drm-phase1
spec:
  podSelector:
    matchLabels:
      app: claude-terminal
  policyTypes:
    - Egress

  egress:
    # DNS 허용 (필수)
    - ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP

    # auth-gateway 내부 통신 (API + CONNECT 프록시)
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: platform
      ports:
        - port: 8000   # auth-gateway API
          protocol: TCP
        - port: 3128   # CONNECT 프록시 (개발 도구용)
          protocol: TCP

    # OnlyOffice Document Server (claude-sessions 내)
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: claude-sessions
          podSelector:
            matchLabels:
              app: onlyoffice
      ports:
        - port: 80
          protocol: TCP

    # RDS ReadOnly Replica (Safety DB 접근)
    - to:
        - ipBlock:
            cidr: 10.0.0.0/8   # VPC 내부 CIDR
      ports:
        - port: 5432
          protocol: TCP

    # Redis (ElastiCache)
    - to:
        - ipBlock:
            cidr: 10.0.0.0/8
      ports:
        - port: 6379
          protocol: TCP

    # HTTPS 외부 접근 — CONNECT 프록시 경유만 허용
    # (직접 443/80 차단됨 → auth-gateway proxy:3128 통해서만 npm/pip/github 접근 가능)
```

- [ ] **Step 2: 카나리 검증 (단일 Pod)**

```bash
# 기존 정책 확인
kubectl get networkpolicy -n claude-sessions

# 카나리 적용 (레이블 선택 제한 방식으로 1개 Pod에만 먼저 적용)
kubectl apply -f infra/k8s/network-policy-drm.yaml

# 적용 후 테스트 Pod에서 외부 접근 확인
kubectl exec -n claude-sessions <pod-name> -- curl -s --connect-timeout 5 https://example.com
# Expected: 연결 실패 (timeout)

kubectl exec -n claude-sessions <pod-name> -- curl -s --connect-timeout 5 http://auth-gateway.platform.svc.cluster.local:8000/health
# Expected: 200 OK (내부 통신 허용)
```

- [ ] **Step 3: 전체 적용 확인**

```bash
kubectl get networkpolicy -n claude-sessions
# Expected: drm-terminal-pod-egress 정책 표시

kubectl describe networkpolicy drm-terminal-pod-egress -n claude-sessions
# Expected: Egress 규칙 올바르게 표시
```

- [ ] **Step 4: 커밋**

```bash
git add infra/k8s/network-policy-drm.yaml
git commit -m "feat(drm): 터미널 Pod egress NetworkPolicy 적용 — 외부 직접 파일 전송 차단"
```

---

## Task 7: auth-gateway 빌드 + 배포

- [ ] **Step 1: 전체 테스트 통과 확인**

```bash
cd auth-gateway
pytest tests/ -v -q
# Expected: 모든 테스트 통과 (새 DRM 테스트 포함)
```

- [ ] **Step 2: Docker 빌드 + ECR Push**

```bash
ECR_REPO="680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway"
cd auth-gateway
docker build --platform linux/amd64 -t auth-gateway:latest .
docker tag auth-gateway:latest ${ECR_REPO}:latest
docker push ${ECR_REPO}:latest
```

- [ ] **Step 3: K8s 롤아웃**

```bash
kubectl -n platform rollout restart deployment/auth-gateway
kubectl -n platform rollout status deployment/auth-gateway --timeout=120s
# Expected: deployment "auth-gateway" successfully rolled out
```

- [ ] **Step 4: 엔드포인트 동작 확인**

```bash
# 뷰어 엔드포인트 존재 확인
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer <token>" \
  https://claude.skons.net/api/v1/secure/view/nonexistent
# Expected: 404 (인증 통과, 파일 없음)

# 직접 다운로드 차단 확인
curl -s -X POST \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"vault_id": "test"}' \
  https://claude.skons.net/api/v1/secure/get
# Expected: {"detail": "직접 파일 다운로드는 DRM 정책에 의해 차단됩니다..."}
```

- [ ] **Step 5: 최종 커밋 태그**

```bash
git tag drm-phase1-complete
git push origin main --tags
```

---

## 자기 검토 결과

**스펙 커버리지:**
- ✅ OnlyOffice 다운로드/인쇄/복사 금지 — Task 2
- ✅ Office 파일 뷰어 라우팅 — Task 1, 4
- ✅ 소스코드 Code Viewer — Task 4 (Monaco 스타일 인라인 HTML)
- ✅ 이미지 Exif 제거 후 인라인 — Task 4
- ✅ 열람 불가 바이너리 차단 — Task 4
- ✅ `secure_get` 직접 다운로드 차단 — Task 5
- ✅ DRM HTTP 보안 헤더 — Task 3
- ✅ 터미널 Pod egress 차단 — Task 6

**미구현 (Phase 2~4로 위임):**
- DEK Envelope 암호화 — Phase 2 계획
- 반출 승인 워크플로 — Phase 3 계획
- 이상탐지 worker — Phase 4 계획
- OpenWebUI 첨부파일 다운로드 버튼 UI 제거 — Phase 1 minor (별도 OpenWebUI 설정 변경 필요, 현재 auth-gateway 범위 외)

**타입/메서드 일관성:**
- `ViewerType` enum: Task 1에서 정의, Task 4에서 사용 ✅
- `_drm_response_headers()`: Task 3에서 정의, Task 4에서 호출 ✅
- `_build_onlyoffice_config(drm_mode=True)`: Task 2에서 정의, Task 4에서 사용 ✅
- `_create_file_token`, `_file_tokens`: 기존 `viewers.py` import ✅
