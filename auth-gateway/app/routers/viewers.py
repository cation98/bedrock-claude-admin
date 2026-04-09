"""파일 뷰어 API — Pod fileserver 프록시 + OnlyOffice iframe.

Endpoints:
  GET /api/v1/viewers/file/{username}/{file_path:path} -- Pod 파일 스트리밍 (인라인)
  GET /api/v1/viewers/office/{username}/{file_path:path} -- OnlyOffice iframe 뷰어 HTML
"""

import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from kubernetes import client

from app.core.config import Settings, get_settings
from app.core.security import decode_token

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
    current_user: dict = Depends(_get_viewer_user),
    settings: Settings = Depends(get_settings),
):
    """Pod fileserver에서 파일을 프록시하여 인라인 스트리밍.

    접근 제어: 본인 Pod 또는 admin만 허용.
    """
    requesting = current_user.get("sub", "")
    is_admin = current_user.get("role") == "admin"
    if not is_admin and requesting.upper() != username.upper():
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    normalized = os.path.normpath(file_path)
    if ".." in normalized or normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")

    pod_ip = _get_pod_ip(username, settings.k8s_namespace)
    download_url = f"http://{pod_ip}:8080/api/download?path={file_path}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(download_url)
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
    """OnlyOffice DocumentServer iframe 뷰어 HTML 반환."""
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
    # OnlyOffice document type
    doc_type = "cell" if ext in {".xlsx", ".xls", ".csv", ".ods"} else \
               "slide" if ext in {".pptx", ".ppt", ".odp"} else "word"

    file_url = f"https://claude.skons.net/api/v1/viewers/file/{username}/{file_path}"
    onlyoffice_url = "https://claude.skons.net/onlyoffice"

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{basename} — Otto AI Viewer</title>
<style>
  body {{ margin:0; padding:0; overflow:hidden; background:#1e1e2e; }}
  #placeholder {{ display:flex; align-items:center; justify-content:center;
    height:100vh; color:#cdd6f4; font-family:'Segoe UI',sans-serif; font-size:16px; }}
</style>
</head><body>
<div id="placeholder">Loading {basename}...</div>
<script src="{onlyoffice_url}/web-apps/apps/api/documents/api.js"></script>
<script>
var config = {{
  document: {{
    fileType: "{ext.lstrip('.')}",
    title: "{basename}",
    url: "{file_url}",
    permissions: {{ download: false, edit: false, print: false, review: false }}
  }},
  documentType: "{doc_type}",
  editorConfig: {{
    mode: "view",
    callbackUrl: "",
    customization: {{
      toolbarNoTabs: true,
      compactHeader: true,
      hideRightMenu: true
    }}
  }},
  type: "embedded",
  height: "100%",
  width: "100%"
}};
document.getElementById("placeholder").remove();
new DocsAPI.DocEditor("placeholder", config);
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
    download_url = f"http://{pod_ip}:8080/api/download?path={file_path}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(download_url)
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
