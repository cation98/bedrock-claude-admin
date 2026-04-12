"""파일 뷰어 API — Pod fileserver 프록시 + OnlyOffice 뷰어.

Endpoints:
  GET /api/v1/viewers/file/{username}/{file_path:path} -- Pod 파일 스트리밍 (인라인)
  GET /api/v1/viewers/onlyoffice/config/{filename}     -- OnlyOffice 설정 JSON
  POST /api/v1/viewers/onlyoffice/callback              -- OnlyOffice 콜백
  GET /api/v1/viewers/markdown/{username}/{file_path:path} -- Markdown HTML 뷰어
"""

import hashlib
import json
import logging
import os
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from jose import jwt as jose_jwt
from kubernetes import client
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal, get_db
from app.core.security import decode_token, get_current_user_or_pod
from app.models.edit_session import EditSession
from app.models.file_share import FileShareACL, SharedDataset

# 임시 파일 토큰: Redis(분산) → 메모리(fallback)
_file_tokens: dict[str, dict] = {}  # fallback: token → {"username", "file_path", "expires"}

router = APIRouter(prefix="/api/v1/viewers", tags=["viewers"])
logger = logging.getLogger(__name__)


async def _get_viewer_user(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    """뷰어 전용 인증 — Bearer 토큰 + claude_token 쿠키 둘 다 지원.

    window.open()으로 열리는 뷰어는 Authorization 헤더가 없으므로 쿠키 필수.
    """
    # 1. Authorization Bearer 토큰
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        payload = decode_token(auth_header.split(" ", 1)[1], settings)
        if payload:
            return payload

    # 2. claude_token 쿠키
    token = request.cookies.get("claude_token", "")
    if token:
        payload = decode_token(token, settings)
        if payload:
            return payload

    # P2-iter3 #6: get_current_user_or_pod(/config 경로) 와 403 으로 통일.
    # 프론트는 두 경로 모두 "인증 실패 → 로그인 리디렉션" 로 동일 처리하므로
    # 상태 코드가 달라지면 분기 로직만 늘어나고 이득이 없다.
    raise HTTPException(status_code=403, detail="Not authenticated")


def _create_file_token(username: str, file_path: str, ttl_seconds: int = 300) -> str:
    """임시 파일 접근 토큰 생성 (5분 TTL). Redis 우선, fallback 메모리.

    상용(2-replica)에서는 Redis가 필수. Redis 장애 시 메모리 fallback은
    단일 replica 환경(로컬 개발)에서만 안정적.
    """
    token = secrets.token_urlsafe(32)
    value = json.dumps({"username": username, "file_path": file_path})
    # P2-BUG4 diag: 토큰 생성 시점 로그 — 이후 /file/ 요청의 prefix와 대조해 1 vs N회 fetch 판별.
    logger.info(f"file_token_created path={file_path} token_prefix={token[:8]} ttl={ttl_seconds}s")
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        if r:
            r.setex(f"ftoken:{token}", ttl_seconds, value)
            return token
    except Exception:
        pass
    # fallback: 메모리 (로컬 개발 전용 — 2-replica 환경에서는 50% 실패 가능)
    logger.warning("Redis unavailable, using memory fallback for file token (unsafe for multi-replica)")
    _file_tokens[token] = {
        "username": username,
        "file_path": file_path,
        "expires": time.time() + ttl_seconds,
    }
    return token


def _consume_file_token(token: str) -> dict | None:
    """토큰 검증 (TTL 기반 재사용 가능). Redis 우선, fallback 메모리.

    P2-BUG4(H1): OnlyOffice Document Server는 Word/PPTX 원본을 변환 파이프라인에서
    여러 번 fetch한다(2026-04-12 로그로 확증: .docx/.pptx 3회, .xlsx 1회).
    이전의 1회용(getdel/pop) 의미는 2차 fetch부터 401을 발생시켜 Word/PPTX 로드를
    실패시키므로, TTL 5분 동안 재검증 가능한 semantics로 전환.
    TTL 만료 시 Redis TTL(setex) / 메모리 GC로 자연 무효화.
    """
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        if r:
            val = r.get(f"ftoken:{token}")  # 재사용 가능 — 삭제하지 않음
            if val:
                return json.loads(val)
    except Exception:
        pass
    # fallback: 메모리
    now = time.time()
    expired = [k for k, v in _file_tokens.items() if v.get("expires", 0) <= now]
    for k in expired:
        _file_tokens.pop(k, None)
    data = _file_tokens.get(token)  # 재사용 가능 — 삭제하지 않음
    if data and data.get("expires", 0) > now:
        return data
    return None


MIME_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
}

OFFICE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".docx", ".doc", ".pptx", ".ppt", ".odt", ".ods", ".odp", ".rtf"}

# 편집 가능한 확장자 (OnlyOffice가 쓰기를 지원하는 형식만)
# .csv/.rtf는 편집 후 저장 시 포맷 손실이 심하므로 view-only로만 허용
EDITABLE_EXTENSIONS = {".xlsx", ".docx", ".pptx", ".odt", ".ods", ".odp"}


# ---------------------------------------------------------------------------
# OnlyOffice Document Key & Edit Session 관리
# ---------------------------------------------------------------------------

def _doc_key_personal(username: str, file_path: str, version: int, salt: str = "") -> str:
    """개인 파일 document key — sha256(personal:{username}:{path}:{version}[:salt]) 앞 20자.

    salt(P2-BUG1): 재편집 시 OnlyOffice Document Server 캐시 충돌 방지를 위해
    새 세션마다 임의 값을 섞는다. 뷰어 fallback 같이 DB 조회 없이 key 가
    필요할 때는 salt 생략(빈 문자열) — 기존 시그니처 호환.
    """
    suffix = f":{salt}" if salt else ""
    raw = f"personal:{username.upper()}:{file_path}:{version}{suffix}".encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def _doc_key_shared(mount_id: int, file_path: str, version: int, salt: str = "") -> str:
    """공유 파일 document key — sha256(shared:{mount_id}:{path}:{version}[:salt]) 앞 20자.

    salt 의미는 `_doc_key_personal` 과 동일.
    """
    suffix = f":{salt}" if salt else ""
    raw = f"shared:{mount_id}:{file_path}:{version}{suffix}".encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def _lookup_edit_session(
    db: Session,
    *,
    is_shared: bool,
    owner_username: str,
    file_path: str,
    mount_id: int | None,
    for_update: bool = False,
) -> EditSession | None:
    """(owner/path/mount) 조합으로 현재 활성 EditSession을 조회.

    활성 기준: status in (editing, saving). save_failed/saved/error는 새 세션으로 취급.
    for_update=True 시 SELECT ... FOR UPDATE (2-replica 환경에서 콜백 동시성 잠금).
    """
    q = db.query(EditSession).filter(
        EditSession.owner_username == owner_username,
        EditSession.file_path == file_path,
        EditSession.is_shared == is_shared,
        EditSession.status.in_(["editing", "saving"]),
    )
    if is_shared:
        q = q.filter(EditSession.mount_id == mount_id)
    else:
        q = q.filter(EditSession.mount_id.is_(None))
    if for_update:
        q = q.with_for_update()
    return q.order_by(EditSession.id.desc()).first()


def _next_version_for(
    db: Session,
    *,
    is_shared: bool,
    owner_username: str,
    file_path: str,
    mount_id: int | None,
) -> int:
    """동일 (is_shared, owner, path, mount) 조합의 과거 세션 중 max(version)+1.

    이전 편집 세션이 status=saved/save_failed/error로 남아 있으면 version=1로
    재삽입 시 unique(document_key) 충돌이 발생한다. 재편집은 항상 새 version.
    """
    from sqlalchemy import func

    q = db.query(func.max(EditSession.version)).filter(
        EditSession.owner_username == owner_username,
        EditSession.file_path == file_path,
        EditSession.is_shared == is_shared,
    )
    if is_shared:
        q = q.filter(EditSession.mount_id == mount_id)
    else:
        q = q.filter(EditSession.mount_id.is_(None))
    max_version = q.scalar()
    return (max_version or 0) + 1


def _get_or_create_edit_session(
    db: Session,
    *,
    is_shared: bool,
    owner_username: str,
    file_path: str,
    mount_id: int | None,
    first_editor_username: str | None = None,
) -> tuple[EditSession, bool]:
    """활성 EditSession을 조회하거나 새로 생성.

    Returns:
        (session, created): created=True이면 방금 새로 만든 세션.

    재편집 시 version은 max(existing)+1로 계산 (#3 fix).
    동시 생성 race는 IntegrityError 포착 → re-SELECT FOR UPDATE로 방어 (#10 fix).
    """
    from sqlalchemy.exc import IntegrityError

    existing = _lookup_edit_session(
        db,
        is_shared=is_shared,
        owner_username=owner_username,
        file_path=file_path,
        mount_id=mount_id,
        for_update=True,
    )
    if existing:
        return existing, False

    version = _next_version_for(
        db,
        is_shared=is_shared,
        owner_username=owner_username,
        file_path=file_path,
        mount_id=mount_id,
    )
    # P2-BUG1: saved 행이 DELETE 로 정리되면 max(version) 이 리셋되어
    # 이전 세션과 같은 version 이 재사용될 수 있다. OnlyOffice Document Server
    # 가 이전 key 의 cached content 를 보고 "버전 변경" 경고 + view-only 로
    # 강제 전환하는 것을 방지하기 위해 세션마다 임의 salt 를 섞는다.
    salt = secrets.token_hex(4)
    if is_shared and mount_id is not None:
        document_key = _doc_key_shared(mount_id, file_path, version, salt=salt)
    else:
        document_key = _doc_key_personal(owner_username, file_path, version, salt=salt)

    session = EditSession(
        document_key=document_key,
        file_path=file_path,
        owner_username=owner_username,
        is_shared=is_shared,
        mount_id=mount_id,
        status="editing",
        version=version,
        first_editor_username=first_editor_username,
    )
    db.add(session)
    try:
        db.flush()
    except IntegrityError:
        # 다른 replica가 같은 document_key로 먼저 insert한 경우.
        # 롤백 후 FOR UPDATE로 재조회하여 그 세션에 합류.
        db.rollback()
        winner = _lookup_edit_session(
            db,
            is_shared=is_shared,
            owner_username=owner_username,
            file_path=file_path,
            mount_id=mount_id,
            for_update=True,
        )
        if winner is None:
            # 이론상 도달 불가 — 충돌했는데 lookup에 없으면 즉시 에러.
            raise
        return winner, False
    return session, True


def _get_pod_ip(username: str, namespace: str = "claude-sessions") -> str:
    """K8s API로 사용자 Pod의 IP 조회.

    Pod 이름 정규화는 K8sService._pod_name과 동일 규칙(lower + _→-) 유지.
    """
    v1 = client.CoreV1Api()
    pod_name = f"claude-terminal-{username.lower().replace('_', '-')}"
    try:
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        if pod.status and pod.status.pod_ip:
            return pod.status.pod_ip
    except client.ApiException:
        pass
    raise HTTPException(status_code=404, detail=f"Pod not found for {username}")


# ---------------------------------------------------------------------------
# 파일 스트리밍 (Pod proxy)
# ---------------------------------------------------------------------------

@router.get("/file/{username}/{file_path:path}")
async def stream_file(
    username: str,
    file_path: str,
    request: Request,
    token: str = Query(default=None),
    settings: Settings = Depends(get_settings),
):
    """Pod fileserver에서 파일을 프록시하여 인라인 스트리밍.

    접근 제어:
    - 일반: Bearer/cookie 인증 (본인 Pod 또는 admin)
    - OnlyOffice: ?token= 임시 토큰 (서버 측 파일 다운로드용, 5분 TTL)
    """
    if token:
        token_data = _consume_file_token(token)
        # P2-BUG4 diag: OO DS 다중 fetch 가설 검증용 로그. H1 확정 후 제거 예정.
        logger.info(f"file_token_request path={file_path} token_prefix={token[:8]} consumed={token_data is not None}")
        if not token_data:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        if token_data["username"].upper() != username.upper() or token_data["file_path"] != file_path:
            raise HTTPException(status_code=403, detail="Token mismatch")
    else:
        current_user = await _get_viewer_user(request, settings)
        requesting = current_user.get("sub", "")
        is_admin = current_user.get("role") == "admin"
        if not is_admin and requesting.upper() != username.upper():
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    normalized = os.path.normpath(file_path)
    if ".." in normalized or normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")

    pod_ip = _get_pod_ip(username, settings.k8s_namespace)
    download_url = f"http://{pod_ip}:8080/api/download"

    import unicodedata
    import urllib.parse

    # 스트리밍 전송 — 전체 파일을 메모리에 버퍼링하지 않고 64KB 청크로 중계.
    # 50MB 파일까지 지원하려면 timeout도 넉넉하게(120s). client/response의 생명주기는
    # StreamingResponse의 generator가 책임지고 종료 시 aclose 한다.
    http = httpx.AsyncClient(timeout=120.0)
    resp: httpx.Response | None = None
    try:
        encoded = urllib.parse.quote(file_path, safe="/")
        req = http.build_request("GET", f"{download_url}?path={encoded}")
        resp = await http.send(req, stream=True)

        if resp.status_code == 404:
            await resp.aclose()
            alt_form = "NFD" if file_path == unicodedata.normalize("NFC", file_path) else "NFC"
            alt_path = unicodedata.normalize(alt_form, file_path)
            alt_encoded = urllib.parse.quote(alt_path, safe="/")
            req = http.build_request("GET", f"{download_url}?path={alt_encoded}")
            resp = await http.send(req, stream=True)

        if resp.status_code != 200:
            status = resp.status_code
            await resp.aclose()
            await http.aclose()
            raise HTTPException(status_code=status, detail="File not accessible")
    except httpx.RequestError as e:
        if resp is not None:
            await resp.aclose()
        await http.aclose()
        logger.error(f"Pod proxy error: {e}")
        raise HTTPException(status_code=502, detail="Pod fileserver unreachable")
    except HTTPException:
        raise
    except Exception:
        if resp is not None:
            await resp.aclose()
        await http.aclose()
        raise

    ext = os.path.splitext(file_path)[1].lower()
    media_type = MIME_MAP.get(ext, "application/octet-stream")
    basename = os.path.basename(file_path)

    # P2-BUG4 H4: 한글 등 비-ASCII 파일명이 HTTP 헤더 latin-1 인코딩에 실패해
    # OnlyOffice Word/PPTX 다운로드가 500으로 죽는 문제 해결.
    # RFC 5987: 구형 클라이언트용 ASCII fallback(filename=) + UTF-8 인코딩(filename*=)
    ascii_fallback = basename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    utf8_encoded = urllib.parse.quote(basename, safe="")
    disposition = f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{utf8_encoded}"

    fwd_headers = {
        "Content-Disposition": disposition,
        "Content-Security-Policy": "sandbox",
        "X-Content-Type-Options": "nosniff",
    }
    # Content-Length를 전달해 브라우저가 진행률 표시 가능하게 한다
    cl = resp.headers.get("content-length")
    if cl:
        fwd_headers["Content-Length"] = cl

    async def _stream() -> "AsyncIterator[bytes]":  # type: ignore[name-defined]
        try:
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                yield chunk
        finally:
            await resp.aclose()
            await http.aclose()

    return StreamingResponse(
        content=_stream(),
        media_type=media_type,
        headers=fwd_headers,
    )


# ---------------------------------------------------------------------------
# OnlyOffice Document Server 통합
# ---------------------------------------------------------------------------

def _onlyoffice_doc_type(ext: str) -> str:
    """확장자 → OnlyOffice documentType 매핑."""
    if ext in {".xlsx", ".xls", ".csv", ".ods"}:
        return "cell"
    if ext in {".pptx", ".ppt", ".odp"}:
        return "slide"
    return "word"


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
) -> dict:
    """OnlyOffice config dict 생성 (config API + HTML viewer 공용).

    Args:
        filename: 표시용 파일명 (확장자 포함)
        username: 현재 사용자 (OnlyOffice UI에 표시될 편집자 ID)
        display_name: 사용자 표시명
        editable: True이면 permissions.edit=True + mode="edit", False이면 view-only
        shared: True이면 co-editing UI 활성화(chat, comments)
        document_key: 외부에서 계산한 key. 제공되지 않으면 기존 방식(fallback)으로 계산
        file_download_url: Document Server가 원본 파일을 다운로드할 URL. None이면 기본 규칙으로 생성
        file_owner_username: 파일 소유자(공유 파일의 경우 현재 사용자와 다름). token 발급 시 사용
    """
    ext = os.path.splitext(filename)[1].lower()
    doc_type = _onlyoffice_doc_type(ext)

    # document key — 외부 지정(DB 버전 기반) 우선, 없으면 과거 방식으로 fallback
    doc_key = document_key or hashlib.sha256(
        f"{username}:{filename}:{int(time.time()//300)}".encode()
    ).hexdigest()[:20]

    # 파일 다운로드 URL — 외부 지정 우선. 기본은 현재 사용자 Pod에서 받기 (기존 동작 호환)
    if file_download_url is None:
        token_owner = file_owner_username or username
        file_token = _create_file_token(token_owner, filename)
        file_download_url = (
            f"http://auth-gateway.platform.svc.cluster.local"
            f"/api/v1/viewers/file/{token_owner}/{filename}?token={file_token}"
        )

    # 편집 불가 확장자는 강제 view-only (OnlyOffice가 저장 시 포맷 손실)
    if editable and ext not in EDITABLE_EXTENSIONS:
        editable = False

    permissions = {
        "download": editable,   # 편집 중이면 다운로드 허용 (편집자 UX)
        "edit": editable,
        "print": editable,
        "review": editable,
        "comment": editable,
        "fillForms": editable,
        "modifyFilter": editable,
        "modifyContentControl": editable,
    }

    customization = {
        "toolbarNoTabs": not editable,        # 편집 시 탭 표시
        "compactHeader": not editable,
        "hideRightMenu": not editable,
        "chat": bool(shared and editable),    # 공유 co-editing에서만 채팅
        "comments": bool(editable),
        "forcesave": bool(editable),          # 편집 시 Ctrl+S/명시적 저장 활성
    }

    config: dict = {
        "document": {
            "fileType": ext.lstrip("."),
            "key": doc_key,
            "title": filename,
            "url": file_download_url,
            "permissions": permissions,
        },
        "documentType": doc_type,
        "editorConfig": {
            "mode": "edit" if editable else "view",
            "callbackUrl": (
                "http://auth-gateway.platform.svc.cluster.local"
                "/api/v1/viewers/onlyoffice/callback"
            ),
            "lang": "ko",
            "user": {
                "id": username,
                "name": display_name,
            },
            "customization": customization,
        },
        "type": "desktop",
        "height": "100%",
        "width": "100%",
    }

    if settings.onlyoffice_jwt_secret:
        token = jose_jwt.encode(config, settings.onlyoffice_jwt_secret, algorithm="HS256")
        config["token"] = token

    return config


def _render_onlyoffice_html(filename: str, config: dict) -> str:
    """OnlyOffice DocEditor를 임베드하는 HTML 페이지 생성."""
    import html as html_mod
    basename = html_mod.escape(os.path.basename(filename))
    config_json = json.dumps(config, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{basename} — Otto AI Viewer</title>
<style>
  html, body {{ margin:0; padding:0; height:100%; overflow:hidden; background:#1e1e2e; }}
  #editor-container {{ width:100%; height:100vh; }}
  .loading {{ display:flex; align-items:center; justify-content:center;
    height:100vh; color:#cdd6f4; font-family:'Segoe UI',sans-serif; font-size:16px; }}
  .err {{ color:#f87171; text-align:center; padding:40px; font-size:14px; }}
</style>
</head><body>
<div id="editor-container"></div>
<script src="/onlyoffice/web-apps/apps/api/documents/api.js"></script>
<script>
(function() {{
  try {{
    var config = {config_json};
    config.type = "desktop";
    config.height = "100%";
    config.width = "100%";
    new DocsAPI.DocEditor("editor-container", config);
  }} catch(e) {{
    var el = document.getElementById("editor-container");
    el.textContent = e.message;
    el.className = "err";
  }}
}})();
</script>
</body></html>"""


@router.get("/onlyoffice/config/{filename:path}")
async def onlyoffice_config(
    filename: str,
    current_user: dict = Depends(get_current_user_or_pod),
    settings: Settings = Depends(get_settings),
):
    """OnlyOffice 뷰어 설정 JSON 반환.

    Hub UI가 이 설정을 받아 OnlyOffice api.js로 iframe을 생성한다.
    JWT secret이 설정되어 있으면 config에 token 필드를 포함한다.
    """
    normalized = os.path.normpath(filename)
    if ".." in normalized or normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in OFFICE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {ext}")

    username = current_user.get("sub", "")
    config = _build_onlyoffice_config(filename, username, current_user.get("name", username), settings)

    return JSONResponse(
        content=config,
        headers={
            "Content-Security-Policy": "frame-ancestors 'self'",
        },
    )


def _validate_office_path(file_path: str) -> str:
    """경로 검증 + 확장자 검증. 허용되면 소문자 확장자 반환."""
    normalized = os.path.normpath(file_path)
    if ".." in normalized or normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in OFFICE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {ext}")
    return ext


def _personal_download_url(username: str, file_path: str) -> str:
    """개인 파일 다운로드용 URL(one-time token 포함) 생성."""
    file_token = _create_file_token(username, file_path)
    return (
        f"http://auth-gateway.platform.svc.cluster.local"
        f"/api/v1/viewers/file/{username}/{file_path}?token={file_token}"
    )


# 라우트 등록 순서 주의: FastAPI/Starlette는 선언 순서대로 매칭하므로
# 더 구체적인 prefix(/edit/, /shared/)가 반드시 /onlyoffice/{username}/... 보다 먼저 와야 한다.
# 아니면 "edit"/"shared"가 username으로 흡수되어 편집/공유 엔드포인트가 호출 불가능해진다.


@router.get("/onlyoffice/edit/{username}/{file_path:path}", response_class=HTMLResponse)
async def onlyoffice_editor(
    username: str,
    file_path: str,
    current_user: dict = Depends(_get_viewer_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """OnlyOffice 편집 모드 — 개인 파일.

    개인 파일은 동시 편집을 지원하지 않는다. 이미 편집 세션이 있으면
    두 번째 사용자는 자동으로 view-only로 열린다(개인 파일 편집 잠금).
    """
    requesting = current_user.get("sub", "")
    is_admin = current_user.get("role") == "admin"
    if not is_admin and requesting.upper() != username.upper():
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    _validate_office_path(file_path)

    # 편집 세션 확보(없으면 생성) — FOR UPDATE로 2-replica 동시 생성 방지.
    # first_editor_username을 함께 기록하여 같은 사용자 재진입 허용 (#5 fix).
    session, created = _get_or_create_edit_session(
        db,
        is_shared=False,
        owner_username=username.upper(),
        file_path=file_path,
        mount_id=None,
        first_editor_username=requesting.upper(),
    )

    # 개인 파일 편집 잠금:
    # - created=True → 내가 첫 편집자 → editable
    # - 기존 세션이고 first_editor == 나 → 재진입 → editable
    # - 그 외(다른 사용자) → view-only
    requesting_upper = requesting.upper()
    editable = bool(created) or (
        session.first_editor_username is not None
        and session.first_editor_username == requesting_upper
    )
    if not editable:
        logger.info(
            f"Personal edit lock: {requesting} opens {file_path} as view-only "
            f"(session {session.id} owned by {session.first_editor_username})"
        )

    db.commit()  # 세션 생성을 확정

    config = _build_onlyoffice_config(
        file_path,
        requesting,
        current_user.get("name", requesting),
        settings,
        editable=editable,
        shared=False,
        document_key=session.document_key,
        file_download_url=_personal_download_url(username, file_path),
        file_owner_username=username,
    )
    return HTMLResponse(content=_render_onlyoffice_html(file_path, config))


@router.get("/onlyoffice/shared/{mount_id}/{file_path:path}", response_class=HTMLResponse)
async def onlyoffice_shared_editor(
    mount_id: int,
    file_path: str,
    current_user: dict = Depends(_get_viewer_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """OnlyOffice 편집 모드 — 공유 파일(co-editing 지원).

    동일 mount_id + file_path + version 조합으로 key가 생성되므로
    두 명 이상이 열면 OnlyOffice가 co-editing 세션으로 합류시킨다.

    ACL 검증은 T5에서 상세화. 여기서는 dataset 존재 + 기본 권한 체크만 수행.
    """
    requesting = current_user.get("sub", "")
    is_admin = current_user.get("role") == "admin"

    # Dataset 조회 (존재 검증 + 소유자 식별)
    dataset = db.query(SharedDataset).filter(SharedDataset.id == mount_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="공유 데이터셋을 찾을 수 없습니다")

    # ACL 검증 — T5에서 share_type(user/team) + share_target 상세 처리.
    # 여기서는 최소한의 방어: 본인 소유가 아니고 admin도 아니면 ACL 통과 필수.
    if not is_admin and dataset.owner_username.upper() != requesting.upper():
        _verify_shared_acl(db, dataset, requesting)

    _validate_office_path(file_path)

    session, _ = _get_or_create_edit_session(
        db,
        is_shared=True,
        owner_username=dataset.owner_username.upper(),
        file_path=file_path,
        mount_id=mount_id,
        first_editor_username=requesting.upper(),
    )
    db.commit()

    # 공유 파일 다운로드 URL: 소유자의 Pod에서 shared-data 경로로 프록시
    # Pod 내부 경로: {username}의 workspace 기준 shared-data/{dataset_name}/{sub_path}
    shared_rel_path = f"shared-data/{dataset.dataset_name}/{file_path}"
    file_token = _create_file_token(dataset.owner_username, shared_rel_path)
    file_download_url = (
        f"http://auth-gateway.platform.svc.cluster.local"
        f"/api/v1/viewers/file/{dataset.owner_username}/{shared_rel_path}?token={file_token}"
    )

    config = _build_onlyoffice_config(
        file_path,
        requesting,
        current_user.get("name", requesting),
        settings,
        editable=True,
        shared=True,
        document_key=session.document_key,
        file_download_url=file_download_url,
        file_owner_username=dataset.owner_username,
    )
    return HTMLResponse(content=_render_onlyoffice_html(file_path, config))


# catch-all 성격이므로 반드시 /edit/, /shared/ 뒤에 등록해야 한다. (L535 주석 참고)
@router.get("/onlyoffice/{username}/{file_path:path}", response_class=HTMLResponse)
async def onlyoffice_viewer(
    username: str,
    file_path: str,
    current_user: dict = Depends(_get_viewer_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """OnlyOffice 뷰어 HTML (view-only) — 개인 파일.

    편집 세션이 이미 진행 중이면 같은 document key + mode='view'로 참여(read-only).
    편집 세션이 없으면 version=1 기준 key로 단독 뷰.
    """
    requesting = current_user.get("sub", "")
    is_admin = current_user.get("role") == "admin"
    if not is_admin and requesting.upper() != username.upper():
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    _validate_office_path(file_path)

    # 현재 활성 편집 세션이 있으면 동일 key로 read-only 합류
    existing = _lookup_edit_session(
        db,
        is_shared=False,
        owner_username=username.upper(),
        file_path=file_path,
        mount_id=None,
    )
    if existing:
        doc_key = existing.document_key
    else:
        # 활성 세션이 없을 때의 뷰어 key — 과거 저장 이력까지 반영해
        # max(version)+1 기반으로 계산. 같은 version 재사용을 피해 OnlyOffice
        # 캐시가 구버전을 서빙하는 문제를 예방한다. (#3 관련)
        fallback_version = _next_version_for(
            db,
            is_shared=False,
            owner_username=username.upper(),
            file_path=file_path,
            mount_id=None,
        )
        doc_key = _doc_key_personal(username, file_path, fallback_version)

    config = _build_onlyoffice_config(
        file_path,
        requesting,  # 현재 보는 사람
        current_user.get("name", requesting),
        settings,
        editable=False,
        shared=False,
        document_key=doc_key,
        file_download_url=_personal_download_url(username, file_path),
        file_owner_username=username,
    )
    return HTMLResponse(content=_render_onlyoffice_html(file_path, config))


def _verify_shared_acl(db: Session, dataset: SharedDataset, requesting_username: str) -> None:
    """공유 데이터셋 접근 권한 검증.

    두 가지 경로 중 하나라도 매칭되면 통과:
      - share_type="user" + share_target == 요청자 사번 (대소문자 무시)
      - share_type="team" + share_target == 요청자 소속 팀명

    revoked_at IS NULL만 활성 ACL. 매칭이 없으면 403.
    """
    from app.models.user import User

    requesting_upper = requesting_username.upper()

    # 1) user 단위 ACL
    user_acl = (
        db.query(FileShareACL)
        .filter(
            FileShareACL.dataset_id == dataset.id,
            FileShareACL.revoked_at.is_(None),
            FileShareACL.share_type == "user",
            FileShareACL.share_target == requesting_upper,
        )
        .first()
    )
    if user_acl:
        return

    # 2) team 단위 ACL — 요청자의 team_name을 조회 후 매칭
    user = (
        db.query(User)
        .filter(User.username == requesting_upper)
        .first()
    )
    if user and user.team_name:
        team_acl = (
            db.query(FileShareACL)
            .filter(
                FileShareACL.dataset_id == dataset.id,
                FileShareACL.revoked_at.is_(None),
                FileShareACL.share_type == "team",
                FileShareACL.share_target == user.team_name,
            )
            .first()
        )
        if team_acl:
            return

    logger.info(
        f"ACL denied: dataset={dataset.id} ({dataset.dataset_name}) "
        f"requester={requesting_upper} team={getattr(user, 'team_name', None)}"
    )
    raise HTTPException(status_code=403, detail="공유 파일 접근 권한이 없습니다")


@router.post("/onlyoffice/callback")
async def onlyoffice_callback(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """OnlyOffice Document Server 콜백 핸들러 — S2S 호출.

    상태 코드별 처리 (OnlyOffice Callback API):
      1: 사용자 연결/해제 — 연결 이벤트 로깅 후 acknowledge
      2: 편집 완료(마지막 사용자 퇴장 10초 후) — 수정 파일 다운로드 + Pod에 쓰기
      3: 저장 에러 — 세션을 error 상태로 전환
      4: 변경 없이 닫힘 — 세션 정리
      6: Force-save — status=2와 동일 저장 로직
      7: Force-save 에러 — 에러 로깅
      10: 기술적 오류 (v8.2+) — 세션을 error로
      기타: {"error": 0} 반환 + warn 로그

    2-replica 환경에서 동일 콜백이 두 번 오면 edit_sessions 행을 FOR UPDATE 잠가
    중복 처리를 방지한다. 응답은 반드시 JSON만 — CSP frame-ancestors 같은
    HTML 관련 헤더는 추가하지 않는다.
    """
    # 요청 body를 한 번 읽어 재사용 (JWT도 body.token에 올 수 있음)
    try:
        body = await request.json()
    except Exception:
        body = {}

    # JWT 검증 — 항상 필수 (secret은 config.py에서 fail-fast 보장)
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    if not token:
        token = body.get("token")
    if not token:
        raise HTTPException(status_code=403, detail="Missing callback token")
    try:
        # P2-iter3 #4: exp 강제. exp 없는/만료된 토큰은 거부하여 replay window 최소화.
        # python-jose 옵션 키는 `require_exp` (개별 claim 별 플래그).
        decoded = jose_jwt.decode(
            token,
            settings.onlyoffice_jwt_secret,
            algorithms=["HS256"],
            options={"require_exp": True, "verify_exp": True},
        )
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid callback token")

    # JWT 클레임이 곧 최종 진실. verified body로 치환.
    # P2-BUG1 (envelope 복원): OnlyOffice 9.x 및 일부 8.x outbox 는 callback JWT 를
    # envelope `{"payload": {status, key, url, ...}, "exp": ...}` 로 서명한다.
    # P2-iter3 #5 에서 "CE 는 flat 만 사용" 이라는 가정 아래 envelope 분기를
    # 제거했으나, 실환경(9.3.1)에서 envelope 이 사용됨이 확인돼 복원. flat/envelope
    # 두 포맷 모두 수용 — envelope 우선, 없으면 flat claim 사용.
    if not isinstance(decoded, dict):
        raise HTTPException(status_code=403, detail="Invalid callback token payload")
    body = decoded.get("payload", decoded) if isinstance(decoded.get("payload"), dict) else decoded

    status = int(body.get("status", 0))
    document_key = body.get("key", "")
    users = body.get("users", [])
    download_url = body.get("url")
    actions = body.get("actions", [])

    logger.info(
        f"OnlyOffice callback: status={status} key={document_key} "
        f"users={users} url={'yes' if download_url else 'no'}"
    )

    if not document_key:
        logger.warning("Callback with no document key — ignoring")
        return JSONResponse(content={"error": 0})

    # DB 세션 (FastAPI의 Depends를 쓰지 않고 직접 관리 — with_for_update + commit 제어)
    db = SessionLocal()
    try:
        # FOR UPDATE로 행 잠금 (2-replica 중복 콜백 처리 방지)
        session = (
            db.query(EditSession)
            .filter(EditSession.document_key == document_key)
            .with_for_update()
            .first()
        )

        if not session:
            logger.warning(f"Callback for unknown document_key={document_key} status={status}")
            return JSONResponse(content={"error": 0})

        # status 1: 연결/해제 로깅만
        if status == 1:
            try:
                _, event_type = _extract_action(actions)
                logger.info(
                    f"Callback status=1 key={document_key} event={event_type} users={users}"
                )
            except Exception:
                pass
            db.commit()
            return JSONResponse(content={"error": 0})

        # status 4: 변경 없이 닫힘 — 행 삭제(P2-BUG1)
        # 이전에는 status="saved" 로 마킹했으나, saved 행이 남으면 재진입 시
        # `_lookup_edit_session` 은 건너뛰지만 `_next_version_for` 가 version 을
        # 계속 키워 DB 에 잔여 행이 누적된다. 변경이 없는 닫힘은 그냥 지운다.
        if status == 4:
            db.delete(session)
            db.commit()
            logger.info(f"Callback status=4 (no change) key={document_key} — session deleted")
            return JSONResponse(content={"error": 0})

        # status 2 / 6: 저장 (2=편집 완료, 6=force-save)
        if status in (2, 6):
            # 중복 콜백 방지 — 이미 saving 중이면 short-circuit OK 반환.
            # FOR UPDATE로 잠근 직후이므로 동시성 안전.
            if session.status == "saving":
                logger.info(
                    f"Callback status={status} key={document_key} ignored — already saving"
                )
                db.commit()
                return JSONResponse(content={"error": 0})

            if not download_url:
                logger.error(f"Callback status={status} without download URL key={document_key}")
                session.status = "save_failed"
                session.last_error = "no download url"
                db.commit()
                return JSONResponse(content={"error": 1})

            session.status = "saving"
            db.commit()  # saving 상태 먼저 확정 (중복 콜백 방지)

            try:
                await _save_edited_file(session, download_url, body.get("filetype"))
            except Exception as exc:
                logger.exception(
                    f"Failed to persist edited file key={document_key}: {exc}"
                )
                # 다시 잠금 획득 후 실패 상태 기록
                session2 = (
                    db.query(EditSession)
                    .filter(EditSession.id == session.id)
                    .with_for_update()
                    .first()
                )
                if session2:
                    session2.status = "save_failed"
                    session2.last_error = str(exc)[:500]
                    db.commit()
                return JSONResponse(content={"error": 1})

            # 성공 처리:
            # - status=2 (편집 완료): DELETE → 재열기 시 새 세션 + 새 key(P2-BUG1)
            # - status=6 (force-save): editing 유지, version 그대로
            #   (편집 세션 연속 중이므로 key를 바꾸면 OnlyOffice 세션이 끊어짐)
            session3 = (
                db.query(EditSession)
                .filter(EditSession.id == session.id)
                .with_for_update()
                .first()
            )
            if session3:
                if status == 2:
                    # 행 자체를 삭제하여 재진입 시 무조건 새 세션 생성.
                    # document_key 의 salt 는 `_get_or_create_edit_session` 에서
                    # 매번 새로 부여되므로 key 충돌은 발생하지 않는다.
                    db.delete(session3)
                    db.commit()
                    logger.info(
                        f"Callback status=2 key={document_key} saved — session deleted"
                    )
                    return JSONResponse(content={"error": 0})
                else:  # status == 6 (force-save)
                    session3.status = "editing"
                    # version 유지 — 편집 지속
                    session3.last_error = None
                    db.commit()
            logger.info(
                f"Callback status={status} key={document_key} saved successfully "
                f"(version={session3.version if session3 else '?'}, "
                f"status={session3.status if session3 else '?'})"
            )
            return JSONResponse(content={"error": 0})

        # status 3: 저장 에러
        if status == 3:
            session.status = "error"
            session.last_error = body.get("error") or "OnlyOffice save error"
            db.commit()
            logger.error(f"Callback status=3 (save error) key={document_key} body={body}")
            return JSONResponse(content={"error": 0})

        # status 7: Force-save 에러
        if status == 7:
            session.last_error = body.get("error") or "OnlyOffice force-save error"
            # 편집은 계속 진행되므로 status는 editing 유지
            db.commit()
            logger.error(f"Callback status=7 (force-save error) key={document_key}")
            return JSONResponse(content={"error": 0})

        # status 10: 기술적 오류 (v8.2+)
        if status == 10:
            session.status = "error"
            session.last_error = body.get("error") or "OnlyOffice technical error"
            db.commit()
            logger.error(f"Callback status=10 (technical error) key={document_key}")
            return JSONResponse(content={"error": 0})

        # 미정의 status — 무시
        logger.warning(f"Unhandled callback status={status} key={document_key}")
        db.commit()
        return JSONResponse(content={"error": 0})
    finally:
        db.close()


def _extract_action(actions: list) -> tuple[str, str]:
    """actions 배열에서 (userid, event_type) 추출. 없으면 ('', '')."""
    if not actions:
        return "", ""
    first = actions[0] if isinstance(actions, list) else {}
    if not isinstance(first, dict):
        return "", ""
    return str(first.get("userid", "")), str(first.get("type", ""))


# SSRF 방어: 콜백 body의 download_url은 OnlyOffice Document Server 서비스 DNS만 허용.
# JWT 검증이 1차 방어선이지만 DS compromise 시나리오를 고려한 심층 방어.
# 허용 호스트는 실제 K8s Service 이름(onlyoffice, claude-sessions ns) + 역호환용
# documentserver alias. localhost는 로컬 개발용.
_ALLOWED_CALLBACK_URL_HOSTS = {
    # 실제 프로덕션 K8s Service DNS
    "onlyoffice.claude-sessions.svc.cluster.local",
    "onlyoffice.claude-sessions.svc",
    "onlyoffice.claude-sessions",
    "onlyoffice",
    # 과거 명칭 호환 (스테이징에서 혹시 남아 있을 수 있음)
    "documentserver.claude-sessions.svc.cluster.local",
    "documentserver.claude-sessions.svc",
    "documentserver.claude-sessions",
    "documentserver",
    # 로컬 개발
    "localhost",
    "127.0.0.1",
}


def _validate_callback_download_url(url: str) -> None:
    """콜백의 download URL이 신뢰 가능한 호스트인지 검증. 아니면 RuntimeError.

    http/https만 허용, host는 allowlist. IMDS(169.254.169.254), K8s API,
    다른 Pod IP 등으로의 SSRF를 원천 차단한다.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(f"disallowed scheme: {parsed.scheme}")
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_CALLBACK_URL_HOSTS:
        raise RuntimeError(f"disallowed callback download host: {host!r}")


async def _save_edited_file(session: EditSession, download_url: str, filetype: str | None) -> None:
    """Document Server에서 수정 파일을 다운로드하여 Pod에 저장.

    진짜 스트리밍: httpx.stream() → tempfile 디스크 쓰기 → kubectl cp (#9 fix).
    메모리 점유는 chunk_size(64KB) 수준으로 상수. 50MB 파일도 메모리 영향 없음.
    SSRF 방어를 위해 download_url 호스트는 allowlist 검증 후에만 호출한다.
    """
    import tempfile as _tempfile
    from urllib.parse import urlparse

    _validate_callback_download_url(download_url)

    # P2-BUG2: OnlyOffice DS(8.2.2)는 자기 서버 이름을 `localhost`로 인식해
    # callback body.url 을 `http://localhost/cache/files/...` 로 생성한다.
    # auth-gateway Pod 에서 `localhost` = 자기 loopback(127.0.0.1) ≠ OnlyOffice Pod
    # → httpx connect 실패. Allowlist 는 통과(`localhost` 포함)하지만 실제 다운로드
    # 가 불가하므로 cluster DNS 로 rewrite 한다. 멱등, 실 localhost 개발환경은
    # K8s 밖이라 영향 없음.
    _parsed = urlparse(download_url)
    if _parsed.hostname in ("localhost", "127.0.0.1"):
        _port_suffix = (
            f":{_parsed.port}" if _parsed.port and _parsed.port != 80 else ""
        )
        _rewritten = _parsed._replace(
            netloc=f"onlyoffice.claude-sessions.svc.cluster.local{_port_suffix}"
        ).geturl()
        logger.warning(
            f"Rewrote OO callback URL from {_parsed.hostname} loopback to cluster DNS: "
            f"{download_url} → {_rewritten}"
        )
        download_url = _rewritten

    logger.info(f"Downloading edited document from OO: {download_url}")

    # Pod 내부 경로 계산 (검증은 K8sService 내부에서)
    if session.is_shared:
        container_path = f"/home/node/workspace/shared-data/{session.file_path}"
    else:
        raw = session.file_path
        container_path = raw if raw.startswith("/") else f"/home/node/workspace/{raw}"

    # Document Server → 로컬 tempfile 스트리밍 다운로드 (메모리 상수)
    fd, tmp_path = _tempfile.mkstemp(prefix="onlyoffice-save-")
    try:
        with os.fdopen(fd, "wb") as out:
            async with httpx.AsyncClient(timeout=120.0) as http:
                async with http.stream("GET", download_url) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(
                            f"download failed: HTTP {resp.status_code} from {download_url}"
                        )
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        out.write(chunk)

        from app.services.k8s_service import K8sService
        from app.core.config import get_settings as _gs
        svc = K8sService(_gs())
        await svc.write_local_file_to_pod(session.owner_username, container_path, tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Markdown 뷰어
# ---------------------------------------------------------------------------

@router.get("/markdown/{username}/{file_path:path}", response_class=HTMLResponse)
async def markdown_viewer(
    username: str,
    file_path: str,
    current_user: dict = Depends(_get_viewer_user),
    settings: Settings = Depends(get_settings),
):
    """Markdown 파일을 HTML로 렌더링하여 표시."""
    requesting = current_user.get("sub", "")
    is_admin = current_user.get("role") == "admin"
    if not is_admin and requesting.upper() != username.upper():
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    normalized = os.path.normpath(file_path)
    if ".." in normalized or normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")

    pod_ip = _get_pod_ip(username, settings.k8s_namespace)
    download_url = f"http://{pod_ip}:8080/api/download"

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            import unicodedata, urllib.parse
            encoded = urllib.parse.quote(file_path, safe="/")
            resp = await http.get(f"{download_url}?path={encoded}")
            if resp.status_code == 404:
                alt_form = "NFD" if file_path == unicodedata.normalize("NFC", file_path) else "NFC"
                alt_path = unicodedata.normalize(alt_form, file_path)
                alt_encoded = urllib.parse.quote(alt_path, safe="/")
                resp = await http.get(f"{download_url}?path={alt_encoded}")
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="File not accessible")

            md_text = resp.text
    except httpx.RequestError as e:
        logger.error(f"Pod proxy error: {e}")
        raise HTTPException(status_code=502, detail="Pod fileserver unreachable")

    import html as html_mod
    import markdown
    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "codehilite", "toc", "nl2br"],
    )
    basename = html_mod.escape(os.path.basename(file_path))

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{basename} — Otto AI Viewer</title>
<style>
  body {{
    font-family: 'Segoe UI', -apple-system, sans-serif;
    background: #0d1117; color: #e6edf3;
    max-width: 860px; margin: 0 auto; padding: 32px 24px;
    line-height: 1.7;
  }}
  h1, h2, h3, h4 {{ color: #58a6ff; margin-top: 1.5em; margin-bottom: 0.5em; }}
  h1 {{ font-size: 1.8rem; border-bottom: 1px solid #30363d; padding-bottom: 8px; }}
  h2 {{ font-size: 1.4rem; border-bottom: 1px solid #21262d; padding-bottom: 6px; }}
  h3 {{ font-size: 1.15rem; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  code {{
    background: #161b22; padding: 2px 6px; border-radius: 4px;
    font-family: 'SFMono-Regular', Consolas, monospace; font-size: 0.9em;
    color: #79c0ff;
  }}
  pre {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 16px; overflow-x: auto; line-height: 1.5;
  }}
  pre code {{ background: none; padding: 0; color: #e6edf3; }}
  table {{
    border-collapse: collapse; width: 100%; margin: 16px 0;
  }}
  th, td {{
    border: 1px solid #30363d; padding: 8px 12px; text-align: left;
  }}
  th {{ background: #161b22; color: #58a6ff; font-weight: 600; }}
  tr:nth-child(even) {{ background: #0d1117; }}
  tr:hover {{ background: #161b22; }}
  blockquote {{
    border-left: 3px solid #58a6ff; padding: 8px 16px; margin: 16px 0;
    background: #161b22; color: #8b949e;
  }}
  img {{ max-width: 100%; border-radius: 8px; }}
  ul, ol {{ padding-left: 24px; }}
  li {{ margin: 4px 0; }}
  hr {{ border: none; border-top: 1px solid #30363d; margin: 24px 0; }}
  .header {{
    display: flex; align-items: center; gap: 12px; margin-bottom: 24px;
    padding-bottom: 12px; border-bottom: 1px solid #30363d;
  }}
  .header .icon {{ font-size: 24px; }}
  .header .title {{ font-size: 1.1rem; color: #8b949e; }}
</style>
</head><body>
<div class="header">
  <span class="icon">📝</span>
  <span class="title">{basename}</span>
</div>
{html_body}
</body></html>"""

    return HTMLResponse(content=html)
