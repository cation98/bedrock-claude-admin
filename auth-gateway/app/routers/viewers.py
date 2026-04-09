"""파일 뷰어 API — Pod fileserver 프록시 + OnlyOffice iframe.

Endpoints:
  GET /api/v1/viewers/file/{username}/{file_path:path} -- Pod 파일 스트리밍 (인라인)
  GET /api/v1/viewers/office/{username}/{file_path:path} -- OnlyOffice iframe 뷰어 HTML
"""

import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from kubernetes import client

from app.core.config import Settings, get_settings
from app.core.security import get_current_user

router = APIRouter(prefix="/api/v1/viewers", tags=["viewers"])
logger = logging.getLogger(__name__)

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
    current_user: dict = Depends(get_current_user),
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
    current_user: dict = Depends(get_current_user),
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
    onlyoffice_url = "http://onlyoffice.claude-sessions.svc.cluster.local"

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
