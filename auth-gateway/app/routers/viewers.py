"""파일 뷰어 API — 다운로드 차단, 인라인 스트리밍 전용.

Endpoints:
  GET /api/v1/viewers/file/{file_path:path} -- 파일 스트리밍 (인라인, 다운로드 차단)

보안 정책:
  - Content-Disposition: inline (attachment 아님 — 다운로드 버튼 차단)
  - Content-Security-Policy: sandbox (스크립트 실행 차단)
  - X-Content-Type-Options: nosniff (MIME 스니핑 차단)
  - Path traversal 검사 (.. 및 절대경로 차단)
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.core.security import get_current_user_or_pod

router = APIRouter(prefix="/api/v1/viewers", tags=["viewers"])
logger = logging.getLogger(__name__)


@router.get("/file/{file_path:path}")
async def stream_file(
    file_path: str,
    current_user: dict = Depends(get_current_user_or_pod),
    settings: Settings = Depends(get_settings),
):
    """파일 스트리밍 — 다운로드 차단, 인라인 표시만 허용.

    경로 검증:
      - '..' 포함 시 400 (디렉토리 트래버설 차단)
      - '/'로 시작하는 절대경로 차단

    파일 소스 우선순위:
      1. S3 Vault 파일: auth-gateway가 S3에서 프록시
      2. Pod 로컬 파일: Pod 에이전트 API를 통해 프록시
      3. (MVP) 플레이스홀더 반환

    응답 헤더:
      Content-Disposition: inline — 브라우저 다운로드 대화창 차단
      Content-Security-Policy: sandbox — iframe 내 스크립트 실행 차단
      X-Content-Type-Options: nosniff — MIME 타입 강제
    """
    # Path traversal 검증
    normalized = os.path.normpath(file_path)
    if ".." in normalized or normalized.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")

    basename = os.path.basename(normalized)

    # MIME 타입 결정
    ext = os.path.splitext(basename)[1].lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
    }
    media_type = mime_map.get(ext, "application/octet-stream")

    logger.info(
        "File stream request: user=%s path=%s",
        current_user.get("sub"),
        file_path,
    )

    # MVP: 플레이스홀더 스트리밍 반환
    # TODO: S3 Vault → boto3 get_object streaming
    # TODO: Pod 로컬 파일 → Pod 에이전트 API 프록시
    return StreamingResponse(
        content=iter([b"PDF content placeholder"]),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{basename}"',
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
