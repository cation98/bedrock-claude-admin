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

from app.core.config import Settings, get_settings
from app.core.security import decode_token, get_current_user_or_pod

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

    raise HTTPException(status_code=401, detail="Not authenticated")


def _create_file_token(username: str, file_path: str, ttl_seconds: int = 300) -> str:
    """임시 파일 접근 토큰 생성 (5분 TTL). Redis 우선, fallback 메모리.

    상용(2-replica)에서는 Redis가 필수. Redis 장애 시 메모리 fallback은
    단일 replica 환경(로컬 개발)에서만 안정적.
    """
    token = secrets.token_urlsafe(32)
    value = json.dumps({"username": username, "file_path": file_path})
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
    """토큰 검증 + 소비 (1회용). Redis 우선, fallback 메모리."""
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        if r:
            val = r.getdel(f"ftoken:{token}")  # 원자적 get+delete
            if val:
                return json.loads(val)
    except Exception:
        pass
    # fallback: 메모리
    now = time.time()
    expired = [k for k, v in _file_tokens.items() if v.get("expires", 0) <= now]
    for k in expired:
        _file_tokens.pop(k, None)
    data = _file_tokens.pop(token, None)
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


def _get_pod_ip(username: str, namespace: str = "claude-sessions") -> str:
    """K8s API로 사용자 Pod의 IP 조회."""
    v1 = client.CoreV1Api()
    pod_name = f"claude-terminal-{username.lower()}"
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

    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
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

            ext = os.path.splitext(file_path)[1].lower()
            media_type = MIME_MAP.get(ext, "application/octet-stream")
            basename = os.path.basename(file_path)

            return StreamingResponse(
                content=iter([resp.content]),
                media_type=media_type,
                headers={
                    "Content-Disposition": f'inline; filename="{basename}"',
                    "Content-Security-Policy": "sandbox",
                    "X-Content-Type-Options": "nosniff",
                },
            )
    except httpx.RequestError as e:
        logger.error(f"Pod proxy error: {e}")
        raise HTTPException(status_code=502, detail="Pod fileserver unreachable")


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
    doc_type = _onlyoffice_doc_type(ext)
    doc_key = hashlib.sha256(f"{username}:{filename}:{int(time.time()//300)}".encode()).hexdigest()[:20]

    # OnlyOffice가 파일을 다운로드할 URL (K8s 내부 DNS — auth-gateway Service)
    file_token = _create_file_token(username, filename)
    file_download_url = f"http://auth-gateway.platform.svc.cluster.local/api/v1/viewers/file/{username}/{filename}?token={file_token}"

    config = {
        "document": {
            "fileType": ext.lstrip("."),
            "key": doc_key,
            "title": filename,
            "url": file_download_url,
            "permissions": {
                "download": False,
                "edit": False,
                "print": False,
                "review": False,
            },
        },
        "documentType": doc_type,
        "editorConfig": {
            "mode": "view",
            "callbackUrl": f"http://auth-gateway.platform.svc.cluster.local/api/v1/viewers/onlyoffice/callback",
            "lang": "ko",
            "user": {
                "id": username,
                "name": current_user.get("name", username),
            },
            "customization": {
                "toolbarNoTabs": True,
                "compactHeader": True,
                "hideRightMenu": True,
                "chat": False,
                "comments": False,
            },
        },
        "type": "embedded",
        "height": "100%",
        "width": "100%",
    }

    # JWT 서명 (OnlyOffice JWT_ENABLED=true인 경우 필수)
    if settings.onlyoffice_jwt_secret:
        token = jose_jwt.encode(config, settings.onlyoffice_jwt_secret, algorithm="HS256")
        config["token"] = token

    return JSONResponse(
        content=config,
        headers={
            "Content-Security-Policy": "frame-ancestors 'self'",
        },
    )


@router.post("/onlyoffice/callback")
async def onlyoffice_callback(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """OnlyOffice 콜백 — S2S 호출이므로 OnlyOffice JWT로 검증.

    OnlyOffice는 문서 상태 변경 시 이 URL을 서버-서버(S2S)로 호출한다.
    Bearer 토큰이나 쿠키가 없으므로 get_current_user_or_pod 사용 불가.
    JWT_ENABLED=true인 경우 요청 body 또는 Authorization 헤더의 JWT로 검증.
    view-only 모드에서는 저장이 없으므로 acknowledgment만 반환.
    """
    if settings.onlyoffice_jwt_secret:
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
        if not token:
            try:
                body = await request.json()
                token = body.get("token")
            except Exception:
                pass
        if not token:
            raise HTTPException(status_code=403, detail="Missing callback token")
        try:
            jose_jwt.decode(token, settings.onlyoffice_jwt_secret, algorithms=["HS256"])
        except Exception:
            raise HTTPException(status_code=403, detail="Invalid callback token")

    return JSONResponse(
        content={"error": 0},
        headers={
            "Content-Security-Policy": "frame-ancestors 'self'",
        },
    )


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
