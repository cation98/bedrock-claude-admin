"""Secure file API — S3 Vault 업로드/다운로드.

Endpoints:
  POST  /api/v1/secure/put              -- 민감 파일을 S3 Vault에 업로드
  POST  /api/v1/secure/get              -- S3 Vault에서 파일 복호화 다운로드
  GET   /api/v1/secure/list             -- 내 vault 파일 목록
  GET   /api/v1/secure/view/{vault_id}  -- DRM 보호 뷰어 (OnlyOffice/코드/이미지)
  GET   /api/v1/secure/vault-content/{vault_id} -- OnlyOffice Document Server 전용 내부 엔드포인트
"""

import base64
import html as _html_lib
import json
import logging
import mimetypes
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import get_current_user_or_pod
from app.models.file_audit import FileAuditLog
from app.models.file_governance import EncryptionState, GovernedFile
from app.services.s3_vault import S3VaultService

# Vault-content short-lived tokens (Redis-or-memory, same pattern as viewers.py)
_vault_tokens: dict[str, dict] = {}

router = APIRouter(prefix="/api/v1/secure", tags=["secure-files"])
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _get_vault_service() -> S3VaultService:
    """설정에서 S3VaultService 인스턴스를 생성한다."""
    settings = get_settings()
    if not settings.s3_vault_bucket or not settings.s3_vault_kms_key_id:
        raise HTTPException(
            status_code=503,
            detail="S3 Vault is not configured (s3_vault_bucket / s3_vault_kms_key_id missing)",
        )
    return S3VaultService(
        bucket_name=settings.s3_vault_bucket,
        kms_key_id=settings.s3_vault_kms_key_id,
        region=settings.s3_vault_region,
    )


def _client_ip(request: Request) -> str:
    """요청 IP 주소 추출."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _create_vault_token(username: str, vault_id: str, ttl_seconds: int = 300) -> str:
    """DRM 뷰어용 단기 vault 접근 토큰 생성 (Redis-or-memory)."""
    token = secrets.token_urlsafe(32)
    value = json.dumps({"username": username, "vault_id": vault_id})
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        if r:
            r.setex(f"vtoken:{token}", ttl_seconds, value)
            return token
    except Exception:
        pass
    _vault_tokens[token] = {
        "username": username,
        "vault_id": vault_id,
        "expires": time.time() + ttl_seconds,
    }
    return token


def _consume_vault_token(token: str) -> dict | None:
    """vault 접근 토큰 검증 (TTL 기반 재사용 가능 — OnlyOffice 다중 fetch 대응)."""
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        if r:
            val = r.get(f"vtoken:{token}")
            if val:
                return json.loads(val)
    except Exception:
        pass
    now = time.time()
    expired = [k for k, v in _vault_tokens.items() if v.get("expires", 0) <= now]
    for k in expired:
        _vault_tokens.pop(k, None)
    data = _vault_tokens.get(token)
    if data and data.get("expires", 0) > now:
        return data
    return None


def _drm_response_headers() -> dict[str, str]:
    """DRM 보호 뷰어 응답에 추가할 HTTP 헤더 반환.

    브라우저 캐시 금지 + 다운로드 방지 정책을 응답 헤더로 강제한다.
    """
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, private",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'self'"
        ),
        "X-DRM-Protected": "1",
    }


# ------------------------------------------------------------------
# Request / Response schemas
# ------------------------------------------------------------------


class SecureGetRequest(BaseModel):
    vault_id: str
    duration_minutes: int = 60


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("/put")
async def secure_put(
    request: Request,
    file: UploadFile = File(...),
    ttl_days: int = 7,
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """민감 파일을 S3 Vault에 업로드.

    - KMS 서버-사이드 암호화로 S3에 저장
    - GovernedFile 레코드 생성 (classification=sensitive, status=active)
    - FileAuditLog 이벤트 기록

    Returns:
        {"vault_id": str, "expires_at": str}
    """
    username = current_user["sub"]
    filename = file.filename or "unnamed"
    file_data = await file.read()
    file_size = len(file_data)

    # 1. S3 DRM 업로드 (AES-256-GCM envelope encryption)
    vault_svc = _get_vault_service()
    try:
        result = vault_svc.upload_file_drm(
            username=username,
            filename=filename,
            file_data=file_data,
            ttl_days=ttl_days,
        )
    except Exception as exc:
        logger.error("Vault upload error for %s: %s", username, exc)
        raise HTTPException(status_code=500, detail=f"Vault upload failed: {exc}")

    vault_id = result["vault_id"]
    s3_key = result["s3_key"]
    expires_at_str = result["expires_at"]
    encrypted_dek = result["encrypted_dek"]

    # expires_at → datetime 파싱
    try:
        expires_at = datetime.fromisoformat(expires_at_str)
    except ValueError:
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

    # 2. GovernedFile 레코드 생성 (DRM 메타데이터 포함)
    governed = GovernedFile(
        username=username,
        filename=filename,
        file_path=s3_key,
        file_type=file.content_type or "application/octet-stream",
        file_size_bytes=file_size,
        classification="sensitive",
        classification_reason="user-uploaded to secure vault",
        status="active",
        ttl_days=ttl_days,
        expires_at=expires_at,
        classified_at=datetime.now(timezone.utc),
        vault_id=vault_id,
        encrypted_dek=encrypted_dek,
        encryption_state=EncryptionState.ENCRYPTED.value,
    )
    db.add(governed)

    # 3. 감사 로그
    audit = FileAuditLog(
        username=username,
        action="vault_upload",
        filename=filename,
        file_path=s3_key,
        detail=f"vault_id={vault_id}, size={file_size}, ttl_days={ttl_days}",
        ip_address=_client_ip(request),
    )
    db.add(audit)
    db.commit()

    logger.info("Secure put: user=%s vault_id=%s filename=%s", username, vault_id, filename)
    return {"vault_id": vault_id, "expires_at": expires_at_str}


@router.post("/get")
async def secure_get(
    body: SecureGetRequest,
    request: Request,
    current_user: dict = Depends(get_current_user_or_pod),
):
    """S3 Vault 직접 다운로드는 DRM 정책에 의해 차단됨.
    원본 파일 열람은 /api/v1/secure/view/{vault_id} 뷰어를 사용하세요.
    """
    raise HTTPException(
        status_code=403,
        detail=(
            "직접 파일 다운로드는 DRM 정책에 의해 차단됩니다. "
            f"뷰어 URL: /api/v1/secure/view/{body.vault_id}"
        ),
    )


@router.get("/list")
async def secure_list(
    current_user: dict = Depends(get_current_user_or_pod),
):
    """내 vault 파일 목록.

    Returns:
        [{"key": str, "size": int, "last_modified": str}, ...]
    """
    username = current_user["sub"]
    vault_svc = _get_vault_service()
    try:
        files = vault_svc.list_user_files(username=username)
    except Exception as exc:
        logger.error("Vault list error for %s: %s", username, exc)
        raise HTTPException(status_code=500, detail=f"Vault list failed: {exc}")

    return files


@router.get("/view/{vault_id}", response_class=HTMLResponse)
async def secure_view(
    vault_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """DRM 보호 뷰어 — 파일 타입에 따라 OnlyOffice/코드/이미지 뷰어 반환.

    - ONLYOFFICE: Document Server가 vault-content 엔드포인트에서 파일 바이트를 fetch
    - CODE: UTF-8 디코딩 후 <pre> 블록으로 인라인 렌더링
    - IMAGE: Pillow Exif 제거 후 base64 인라인 렌더링
    - UNSUPPORTED: 403 반환
    - DRM 응답 헤더로 브라우저 캐시 방지

    Returns:
        HTML page (viewer 타입에 따라 다름)
    """
    from app.services.file_viewer_router import ViewerType, get_viewer_type

    username = current_user["sub"]
    display_name = current_user.get("name", username)

    # GovernedFile 조회 — filename, encrypted_dek, id 획득
    gf = (
        db.query(GovernedFile)
        .filter(
            GovernedFile.username == username,
            GovernedFile.vault_id == vault_id,
        )
        .first()
    )
    if gf is None:
        raise HTTPException(status_code=404, detail="Vault item not found")

    filename = gf.filename
    viewer_type = get_viewer_type(filename)

    if viewer_type == ViewerType.UNSUPPORTED:
        raise HTTPException(status_code=403, detail="File type not supported for viewing")

    # 감사 로그
    audit = FileAuditLog(
        username=username,
        action="vault_view",
        filename=filename,
        file_path=f"vault/{username}/{vault_id}/",
        detail=f"vault_id={vault_id}, viewer={viewer_type.value}, drm_mode=true",
        ip_address=_client_ip(request),
    )
    db.add(audit)
    db.commit()

    if viewer_type == ViewerType.ONLYOFFICE:
        from app.routers.viewers import _build_onlyoffice_config, _render_onlyoffice_html

        tmp_token = _create_vault_token(username, vault_id)
        vault_content_url = (
            f"http://auth-gateway.platform.svc.cluster.local"
            f"/api/v1/secure/vault-content/{vault_id}?token={tmp_token}"
        )
        config = _build_onlyoffice_config(
            filename, username, display_name, settings,
            drm_mode=True, file_download_url=vault_content_url,
        )
        html_content = _render_onlyoffice_html(filename, config)
        logger.info("Secure view (onlyoffice): user=%s vault_id=%s filename=%s", username, vault_id, filename)
        return HTMLResponse(content=html_content, headers=_drm_response_headers())

    # CODE / IMAGE — S3에서 파일 다운로드 필요
    vault_svc = _get_vault_service()
    try:
        file_data, _ = vault_svc.download_file(
            username=username,
            vault_id=vault_id,
            encrypted_dek=gf.encrypted_dek,
            file_id=gf.id,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Vault item not found")
    except Exception as exc:
        logger.error("Vault view download error for %s/%s: %s", username, vault_id, exc)
        raise HTTPException(status_code=500, detail=f"Vault view failed: {exc}")

    if viewer_type == ViewerType.CODE:
        text = file_data.decode("utf-8", errors="replace")
        escaped = _html_lib.escape(text)
        lines_html = "".join(
            f'<span class="lineno">{i}</span>{line}\n'
            for i, line in enumerate(escaped.splitlines(), 1)
        )
        html_content = (
            "<!DOCTYPE html><html><head>"
            '<meta charset="utf-8">'
            f"<title>{_html_lib.escape(filename)}</title>"
            "<style>"
            "body{margin:0;background:#1e1e1e;color:#d4d4d4;font-family:'Courier New',monospace;font-size:13px;}"
            "pre{margin:0;padding:16px;white-space:pre-wrap;word-break:break-all;}"
            ".lineno{color:#858585;user-select:none;min-width:40px;display:inline-block;"
            "text-align:right;padding-right:12px;}"
            "</style>"
            '<script>document.addEventListener("contextmenu",function(e){e.preventDefault();return false;});</script>'
            f"</head><body><pre>{lines_html}</pre></body></html>"
        )
        logger.info("Secure view (code): user=%s vault_id=%s filename=%s", username, vault_id, filename)
        return HTMLResponse(content=html_content, headers=_drm_response_headers())

    # IMAGE
    try:
        import io
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(file_data))
        img = ImageOps.exif_transpose(img)
        buf = io.BytesIO()
        img.save(buf, format=img.format or "PNG")
        clean_bytes = buf.getvalue()
    except Exception:
        clean_bytes = file_data

    content_type = mimetypes.guess_type(filename)[0] or "image/png"
    img_b64 = base64.b64encode(clean_bytes).decode("utf-8")
    html_content = (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        f"<title>{_html_lib.escape(filename)}</title>"
        "<style>"
        "body{margin:0;background:#1a1a1a;display:flex;align-items:center;justify-content:center;min-height:100vh;}"
        "img{max-width:100vw;max-height:100vh;object-fit:contain;user-select:none;-webkit-user-drag:none;}"
        "</style>"
        "</head><body>"
        f'<img src="data:{content_type};base64,{img_b64}" '
        f'alt="{_html_lib.escape(filename)}" oncontextmenu="return false;">'
        "</body></html>"
    )
    logger.info("Secure view (image): user=%s vault_id=%s filename=%s", username, vault_id, filename)
    return HTMLResponse(content=html_content, headers=_drm_response_headers())


@router.get("/vault-content/{vault_id}")
async def vault_content(
    vault_id: str,
    token: str = Query(..., description="Single-use vault access token"),
    db: Session = Depends(get_db),
):
    """OnlyOffice Document Server가 내부 fetch 시 사용하는 원본 파일 바이트 엔드포인트.

    JWT 인증 없이 단기 토큰으로 접근 (Document Server는 JWT를 보내지 않음).
    토큰은 /view 엔드포인트에서 발급하며 5분 TTL 동안 재사용 가능.

    Returns:
        Raw file bytes with correct Content-Type
    """
    token_data = _consume_vault_token(token)
    if token_data is None:
        raise HTTPException(status_code=401, detail="Invalid or expired vault token")

    username = token_data["username"]
    token_vault_id = token_data["vault_id"]

    if token_vault_id != vault_id:
        raise HTTPException(status_code=403, detail="Token vault_id mismatch")

    # GovernedFile 조회 — DRM 파라미터 획득
    gf = (
        db.query(GovernedFile)
        .filter(
            GovernedFile.username == username,
            GovernedFile.vault_id == vault_id,
        )
        .first()
    )
    encrypted_dek = gf.encrypted_dek if gf else None
    file_id = gf.id if gf else None
    filename = gf.filename if gf else "file"

    vault_svc = _get_vault_service()
    try:
        file_data, _ = vault_svc.download_file(
            username=username,
            vault_id=vault_id,
            encrypted_dek=encrypted_dek,
            file_id=file_id,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Vault item not found")
    except Exception as exc:
        logger.error("Vault content fetch error for %s/%s: %s", username, vault_id, exc)
        raise HTTPException(status_code=500, detail=f"Vault content unavailable: {exc}")

    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    logger.info(
        "Vault content served: user=%s vault_id=%s filename=%s size=%d",
        username, vault_id, filename, len(file_data),
    )

    resp_headers = _drm_response_headers()
    resp_headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return Response(
        content=file_data,
        media_type=content_type,
        headers=resp_headers,
    )
