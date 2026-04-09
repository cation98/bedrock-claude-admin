"""파일 뷰어 API — Pod fileserver 프록시 + OnlyOffice iframe.

Endpoints:
  GET /api/v1/viewers/file/{username}/{file_path:path} -- Pod 파일 스트리밍 (인라인)
  GET /api/v1/viewers/office/{username}/{file_path:path} -- OnlyOffice iframe 뷰어 HTML
"""

import hashlib
import json
import logging
import os
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from kubernetes import client

from app.core.config import Settings, get_settings
from app.core.security import decode_token

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
    """임시 파일 접근 토큰 생성 (5분 TTL). Redis 우선, fallback 메모리."""
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
    # fallback: 메모리
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
    # 임시 토큰 인증 (SheetJS 파일 다운로드용)
    if token:
        token_data = _consume_file_token(token)
        if not token_data:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        if token_data["username"].upper() != username.upper() or token_data["file_path"] != file_path:
            raise HTTPException(status_code=403, detail="Token mismatch")
    else:
        # 일반 인증
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
        async with httpx.AsyncClient(timeout=30.0) as http:
            import unicodedata, urllib.parse
            # 공백은 %20으로 인코딩 (+ 아님 — SimpleHTTPRequestHandler 호환)
            encoded = urllib.parse.quote(file_path, safe="/")
            resp = await http.get(f"{download_url}?path={encoded}")
            if resp.status_code == 404:
                # NFC/NFD 변환 재시도
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


@router.get("/office/{username}/{file_path:path}", response_class=HTMLResponse)
async def office_viewer(
    username: str,
    file_path: str,
    current_user: dict = Depends(_get_viewer_user),
    settings: Settings = Depends(get_settings),
):
    """Excel/CSV 뷰어 — SheetJS 클라이언트 사이드 렌더링.

    보안: SheetJS sheet_to_html은 셀 값만 추출하여 HTML table 생성.
    사용자 입력이 아닌 파일 데이터이며, 스크립트 실행은 CSP sandbox로 차단.
    """
    requesting = current_user.get("sub", "")
    is_admin = current_user.get("role") == "admin"
    if not is_admin and requesting.upper() != username.upper():
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    normalized = os.path.normpath(file_path)
    if ".." in normalized or normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in OFFICE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {ext}")

    basename = os.path.basename(file_path)
    # 브라우저에서 Pod fileserver로 직접 다운로드 (auth-gateway 프록시 우회)
    # /files/{pod_name}/ 경로는 nginx Ingress → Pod:8080 직접 라우팅
    # 한글 파일명 인코딩 문제를 브라우저-nginx가 자연스럽게 처리
    import urllib.parse
    pod_name = f"claude-terminal-{username.lower()}"
    encoded_path = urllib.parse.quote(file_path, safe="/")
    file_url = f"/files/{pod_name}/api/download?path={encoded_path}"

    # SheetJS CDN — 브라우저에서 로드 (auth-gateway가 아닌 사용자 브라우저가 접근)
    sheetjs_cdn = "https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js"

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{basename} — Otto AI Viewer</title>
<script src="{sheetjs_cdn}"></script>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'Segoe UI',-apple-system,sans-serif; background:#0d1117; color:#e6edf3; }}
  .hdr {{ padding:12px 20px; background:#161b22; border-bottom:1px solid #30363d;
    display:flex; align-items:center; gap:12px; }}
  .hdr .ttl {{ font-size:14px; color:#8b949e; }}
  .hdr .tabs {{ display:flex; gap:4px; margin-left:auto; }}
  .hdr .tab {{ padding:4px 12px; border-radius:4px; font-size:12px;
    cursor:pointer; background:#21262d; color:#8b949e; border:1px solid #30363d; }}
  .hdr .tab.on {{ background:#264f78; color:#fff; border-color:#58a6ff; }}
  .tw {{ overflow:auto; height:calc(100vh - 48px); }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th {{ background:#161b22; color:#58a6ff; font-weight:600; position:sticky; top:0; z-index:1;
    padding:8px 12px; border:1px solid #30363d; text-align:left; white-space:nowrap; }}
  td {{ padding:6px 12px; border:1px solid #21262d; white-space:nowrap; max-width:400px;
    overflow:hidden; text-overflow:ellipsis; }}
  tr:hover {{ background:#161b22; }}
  .ld {{ display:flex; align-items:center; justify-content:center; height:100vh;
    color:#8b949e; font-size:16px; gap:10px; }}
  .err {{ color:#f85149; text-align:center; padding:40px; font-size:14px; }}
</style>
</head><body>
<div class="ld" id="ld">&#128194; {basename} 로딩 중...</div>
<div id="vw" style="display:none;">
  <div class="hdr">
    <span style="font-size:20px;">&#128202;</span>
    <span class="ttl">{basename}</span>
    <div class="tabs" id="tabs"></div>
  </div>
  <div class="tw" id="tw"></div>
</div>
<script>
(async function() {{
  try {{
    var resp = await fetch("{file_url}");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    var buf = await resp.arrayBuffer();
    var wb = XLSX.read(buf, {{type:"array"}});
    document.getElementById("ld").style.display = "none";
    document.getElementById("vw").style.display = "block";
    var tabs = document.getElementById("tabs");
    var tw = document.getElementById("tw");
    function show(name) {{
      var ws = wb.Sheets[name];
      // sheet_to_html: SheetJS가 셀 값만 추출하여 table 생성 (XSS 안전)
      tw.textContent = "";
      var div = document.createElement("div");
      div.insertAdjacentHTML("afterbegin", XLSX.utils.sheet_to_html(ws, {{editable:false}}));
      tw.appendChild(div);
      tabs.querySelectorAll(".tab").forEach(function(t) {{
        t.className = "tab" + (t.textContent === name ? " on" : "");
      }});
    }}
    wb.SheetNames.forEach(function(n) {{
      var b = document.createElement("button");
      b.className = "tab"; b.textContent = n;
      b.onclick = function() {{ show(n); }};
      tabs.appendChild(b);
    }});
    if (wb.SheetNames.length > 0) show(wb.SheetNames[0]);
  }} catch(e) {{
    document.getElementById("ld").textContent = "\\u26a0 " + e.message;
  }}
}})();
</script>
</body></html>"""

    return HTMLResponse(content=html)


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
            # 공백은 %20으로 인코딩 (+ 아님 — SimpleHTTPRequestHandler 호환)
            encoded = urllib.parse.quote(file_path, safe="/")
            resp = await http.get(f"{download_url}?path={encoded}")
            if resp.status_code == 404:
                # NFC/NFD 변환 재시도
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

    import markdown
    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "codehilite", "toc", "nl2br"],
    )
    basename = os.path.basename(file_path)

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
