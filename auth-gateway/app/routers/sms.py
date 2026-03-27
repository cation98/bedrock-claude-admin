"""SMS 발송 API (Auth Gateway 경유).

Pod에서 직접 SMS 게이트웨이를 호출하지 않고,
Auth Gateway를 경유하여 중앙에서 발송 한도/감사를 관리.

Endpoints:
  POST /api/v1/sms/send — SMS 발송 (인증 필요)
  GET  /api/v1/sms/usage — 오늘의 SMS 사용량 조회
"""

import logging
import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.core.security import get_current_user

router = APIRouter(prefix="/api/v1/sms", tags=["sms"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SMS 발송 기록 테이블
# ---------------------------------------------------------------------------
class SmsLog(Base):
    __tablename__ = "sms_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sender_username = Column(String(50), nullable=False)
    recipient_phone = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String(20), default="sent")  # sent, failed
    error_detail = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# 요청/응답 스키마
# ---------------------------------------------------------------------------
class SmsSendRequest(BaseModel):
    phone_number: str
    message: str

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = re.sub(r"[\s\-\.]", "", v)
        if not re.match(r"^01[016789]\d{7,8}$", cleaned):
            raise ValueError("유효한 한국 휴대폰 번호가 아닙니다")
        return cleaned

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        if len(v.strip()) == 0:
            raise ValueError("메시지가 비어있습니다")
        if len(v) > 80:
            raise ValueError("SMS는 80자 이내여야 합니다")
        return v.strip()


class SmsSendResponse(BaseModel):
    success: bool
    message: str
    remaining_today: int


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
DAILY_LIMIT = 10


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------
def _normalize_phone(phone: str) -> str:
    """010-XXXX-XXXX 형식으로 정규화."""
    cleaned = re.sub(r"[\s\-\.]", "", phone)
    if len(cleaned) == 11:
        return f"{cleaned[:3]}-{cleaned[3:7]}-{cleaned[7:]}"
    elif len(cleaned) == 10:
        return f"{cleaned[:3]}-{cleaned[3:6]}-{cleaned[6:]}"
    return cleaned


def _get_today_count(db: Session, username: str) -> int:
    """오늘 해당 사용자가 발송한 SMS 건수."""
    return (
        db.query(func.count(SmsLog.id))
        .filter(
            SmsLog.sender_username == username,
            func.date(SmsLog.created_at) == datetime.now(timezone.utc).date(),
        )
        .scalar()
    )


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------
@router.post("/send", response_model=SmsSendResponse)
async def send_sms(
    request: SmsSendRequest,
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """SMS 발송 (일일 10건 제한, 감사 로그 기록)."""
    username = current_user["sub"]

    # 일일 발송 한도 확인
    today_count = _get_today_count(db, username)
    if today_count >= DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"일일 발송 한도 초과 ({DAILY_LIMIT}건/일)",
        )

    # SMS 게이트웨이 설정 확인
    sms_url = settings.sms_gateway_url
    sms_auth = settings.sms_auth_string
    sender_number = "02-6123-2200"

    if not sms_url:
        raise HTTPException(
            status_code=503, detail="SMS 서비스가 설정되지 않았습니다"
        )

    # SMS 게이트웨이 페이로드
    formatted_phone = _normalize_phone(request.phone_number)
    import base64 as b64lib
    pw_base64 = b64lib.b64encode(sms_auth.encode()).decode()
    payload = {
        "TranType": "4",
        "TranPhone": formatted_phone,
        "TranCallBack": sender_number,
        "TranMsg": f"[Claude Code] {request.message}",
        "SysPw": pw_base64,
    }

    # 감사 로그 준비
    log_entry = SmsLog(
        sender_username=username,
        recipient_phone=formatted_phone,
        message=request.message,
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(sms_url, json=payload)
            response.raise_for_status()
            data = response.json()

        result = data.get("d", {}).get("Result", {})
        if result.get("ResultCode") != "1":
            log_entry.status = "failed"
            log_entry.error_detail = result.get("ResultMsg", "Unknown error")
            db.add(log_entry)
            db.commit()
            raise HTTPException(
                status_code=502,
                detail=f"SMS 발송 실패: {result.get('ResultMsg')}",
            )

        log_entry.status = "sent"
        db.add(log_entry)
        db.commit()

        remaining = DAILY_LIMIT - today_count - 1
        logger.info(
            "SMS sent by %s to %s (remaining: %d)",
            username,
            formatted_phone,
            remaining,
        )

        return SmsSendResponse(
            success=True,
            message=f"{formatted_phone}로 SMS 발송 완료",
            remaining_today=remaining,
        )

    except httpx.HTTPError as e:
        log_entry.status = "failed"
        log_entry.error_detail = str(e)[:500]
        db.add(log_entry)
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"SMS 게이트웨이 오류: {str(e)[:100]}",
        )


@router.get("/usage")
async def get_sms_usage(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """오늘의 SMS 사용량 조회."""
    username = current_user["sub"]
    today_count = _get_today_count(db, username)
    return {
        "used_today": today_count,
        "limit": DAILY_LIMIT,
        "remaining": DAILY_LIMIT - today_count,
    }
