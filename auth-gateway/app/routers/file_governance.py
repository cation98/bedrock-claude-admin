"""파일 거버넌스 API — 스캔 보고, 대시보드, 파일 관리.

Endpoints:
  POST  /api/v1/governance/scan-report   -- Pod 에이전트가 스캔 결과 보고
  GET   /api/v1/governance/dashboard     -- 거버넌스 대시보드 요약 (관리자 전용)
  GET   /api/v1/governance/files         -- 파일 목록 + 필터 (관리자 전용)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, get_current_user_or_pod
from app.models.file_audit import FileAuditLog
from app.models.file_governance import GovernedFile
from app.services.file_scanner import classify_file

router = APIRouter(prefix="/api/v1/governance", tags=["governance"])
logger = logging.getLogger(__name__)

# TTL 설정 (일 단위)
_TTL_SENSITIVE = 7
_TTL_NORMAL = 30


# ==================== Request / Response 스키마 ====================


class ScanReportFile(BaseModel):
    filename: str
    file_path: str
    file_size_bytes: int = 0
    file_type: str = "unknown"


class ScanReportRequest(BaseModel):
    pod_name: str
    files: list[ScanReportFile]


# ==================== 관리자 권한 헬퍼 ====================


def _require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """관리자 권한 확인."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ==================== Endpoints ====================


@router.post("/scan-report")
async def report_scan(
    request: ScanReportRequest,
    current_user: dict = Depends(get_current_user_or_pod),
    db: Session = Depends(get_db),
):
    """Pod 에이전트가 스캔 결과를 보고한다.

    각 파일에 대해 classify_file()로 분류하고 GovernedFile을 upsert한다.
    동일 username+file_path가 이미 존재하면 업데이트, 없으면 생성.
    TTL 자동 설정: sensitive=7일, normal=30일.
    """
    username = current_user["sub"]
    now = datetime.now(timezone.utc)

    classified_count = 0
    sensitive_count = 0
    normal_count = 0

    for file_info in request.files:
        # 1. 파일 분류
        result = classify_file(
            filename=file_info.filename,
            file_path=file_info.file_path,
            file_size=file_info.file_size_bytes,
        )

        # 2. TTL 계산
        if result.classification == "sensitive":
            ttl_days = _TTL_SENSITIVE
            sensitive_count += 1
        else:
            # normal 또는 unknown → 30일 TTL
            ttl_days = _TTL_NORMAL
            if result.classification == "normal":
                normal_count += 1

        expires_at = now + timedelta(days=ttl_days)

        # 3. Upsert (username + file_path 기준)
        existing = (
            db.query(GovernedFile)
            .filter(
                GovernedFile.username == username,
                GovernedFile.file_path == file_info.file_path,
            )
            .first()
        )

        if existing:
            # 업데이트
            existing.filename = file_info.filename
            existing.file_type = file_info.file_type
            existing.file_size_bytes = file_info.file_size_bytes
            existing.classification = result.classification
            existing.classification_reason = result.reason
            existing.status = "active"
            existing.ttl_days = ttl_days
            existing.expires_at = expires_at
            existing.classified_at = now
            existing.updated_at = now
            governed_file = existing
        else:
            # 신규 생성 — quarantine으로 시작 후 분류 완료 시 active로 변경
            governed_file = GovernedFile(
                username=username,
                filename=file_info.filename,
                file_path=file_info.file_path,
                file_type=file_info.file_type,
                file_size_bytes=file_info.file_size_bytes,
                classification=result.classification,
                classification_reason=result.reason,
                status="active",  # quarantine → classify → active
                ttl_days=ttl_days,
                expires_at=expires_at,
                classified_at=now,
            )
            db.add(governed_file)

        classified_count += 1

        # 4. 감사 로그 기록
        audit = FileAuditLog(
            username=username,
            action="classify",
            filename=file_info.filename,
            file_path=file_info.file_path,
            detail=(
                f"pod={request.pod_name}, "
                f"classification={result.classification}, "
                f"reason={result.reason}, "
                f"ttl_days={ttl_days}"
            ),
        )
        db.add(audit)

    db.commit()

    logger.info(
        f"Scan report from {username} ({request.pod_name}): "
        f"{classified_count} files classified, "
        f"{sensitive_count} sensitive, {normal_count} normal"
    )

    return {
        "classified": classified_count,
        "sensitive": sensitive_count,
        "normal": normal_count,
    }


@router.get("/dashboard")
async def governance_dashboard(
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """거버넌스 대시보드 요약 (관리자 전용).

    - total_files: 전체 active 파일 수
    - sensitive_files: sensitive 분류 파일 수
    - expiring_soon: 7일 이내 만료 예정 파일 수
    - storage_used_bytes: 전체 파일 크기 합계
    """
    now = datetime.now(timezone.utc)
    expiry_cutoff = now + timedelta(days=7)

    total_files = (
        db.query(func.count(GovernedFile.id))
        .filter(GovernedFile.status == "active")
        .scalar()
        or 0
    )

    sensitive_files = (
        db.query(func.count(GovernedFile.id))
        .filter(
            GovernedFile.status == "active",
            GovernedFile.classification == "sensitive",
        )
        .scalar()
        or 0
    )

    expiring_soon = (
        db.query(func.count(GovernedFile.id))
        .filter(
            GovernedFile.status == "active",
            GovernedFile.expires_at <= expiry_cutoff,
            GovernedFile.expires_at > now,
        )
        .scalar()
        or 0
    )

    storage_used_bytes = (
        db.query(func.coalesce(func.sum(GovernedFile.file_size_bytes), 0))
        .filter(GovernedFile.status == "active")
        .scalar()
        or 0
    )

    return {
        "total_files": total_files,
        "sensitive_files": sensitive_files,
        "expiring_soon": expiring_soon,
        "storage_used_bytes": storage_used_bytes,
    }


@router.get("/files")
async def list_governed_files(
    classification: Optional[str] = None,
    status: Optional[str] = None,
    username: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    _admin: dict = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """파일 목록 + 필터 (관리자 전용).

    Query params:
      classification: "sensitive"|"normal"|"unknown" (선택)
      status: "active"|"quarantine"|"expired" (선택)
      username: 특정 사용자 필터 (선택)
      page: 페이지 번호 (1-based)
      per_page: 페이지 당 항목 수
    """
    query = db.query(GovernedFile)

    if classification:
        query = query.filter(GovernedFile.classification == classification)
    if status:
        query = query.filter(GovernedFile.status == status)
    if username:
        query = query.filter(GovernedFile.username == username)

    total = query.count()

    offset = (page - 1) * per_page
    files = (
        query.order_by(GovernedFile.created_at.desc())
        .offset(offset)
        .limit(per_page)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "files": [
            {
                "id": f.id,
                "username": f.username,
                "filename": f.filename,
                "file_path": f.file_path,
                "file_type": f.file_type,
                "file_size_bytes": f.file_size_bytes,
                "classification": f.classification,
                "classification_reason": f.classification_reason,
                "status": f.status,
                "ttl_days": f.ttl_days,
                "expires_at": f.expires_at.isoformat() if f.expires_at else None,
                "classified_at": f.classified_at.isoformat() if f.classified_at else None,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in files
        ],
    }
