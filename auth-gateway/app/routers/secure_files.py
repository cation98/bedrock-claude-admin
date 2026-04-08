"""Secure file API — S3 Vault 업로드/다운로드.

Endpoints:
  POST  /api/v1/secure/put   -- 민감 파일을 S3 Vault에 업로드
  POST  /api/v1/secure/get   -- S3 Vault에서 파일 복호화 다운로드
  GET   /api/v1/secure/list  -- 내 vault 파일 목록
"""

import base64
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user_or_pod
from app.models.file_audit import FileAuditLog
from app.models.file_governance import GovernedFile
from app.services.s3_vault import S3VaultService

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

    # 1. S3 업로드
    vault_svc = _get_vault_service()
    try:
        result = vault_svc.upload_file(
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

    # expires_at → datetime 파싱
    try:
        expires_at = datetime.fromisoformat(expires_at_str)
    except ValueError:
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

    # 2. GovernedFile 레코드 생성
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
    db: Session = Depends(get_db),
):
    """S3 Vault에서 파일 다운로드.

    소유자 확인 후 파일 바이트를 base64로 인코딩하여 반환한다.
    Pod 에이전트는 응답에서 content_b64를 디코딩해 파일로 저장한다.

    Returns:
        {"filename": str, "size": int, "expires_in": int, "content_b64": str}
        with Content-Disposition header set to the original filename
    """
    username = current_user["sub"]

    vault_svc = _get_vault_service()
    try:
        file_data, metadata = vault_svc.download_file(
            username=username,
            vault_id=body.vault_id,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Vault item not found")
    except Exception as exc:
        logger.error("Vault download error for %s/%s: %s", username, body.vault_id, exc)
        raise HTTPException(status_code=500, detail=f"Vault download failed: {exc}")

    filename = metadata.get("original-filename", "file")

    # expires_at 파싱 → expires_in (초)
    expires_in_seconds = body.duration_minutes * 60
    expires_at_str = metadata.get("expires-at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
            expires_in_seconds = max(0, int(remaining))
        except ValueError:
            pass

    # 4. 감사 로그
    audit = FileAuditLog(
        username=username,
        action="vault_download",
        filename=filename,
        file_path=f"vault/{username}/{body.vault_id}/",
        detail=f"vault_id={body.vault_id}, duration_minutes={body.duration_minutes}",
        ip_address=_client_ip(request),
    )
    db.add(audit)
    db.commit()

    logger.info(
        "Secure get: user=%s vault_id=%s filename=%s size=%d",
        username,
        body.vault_id,
        filename,
        len(file_data),
    )

    content_b64 = base64.b64encode(file_data).decode("utf-8")
    return {
        "filename": filename,
        "size": len(file_data),
        "expires_in": expires_in_seconds,
        "content_b64": content_b64,
    }


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
