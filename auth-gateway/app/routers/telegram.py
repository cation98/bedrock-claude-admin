"""텔레그램 봇 API.

임원이 텔레그램에서 자연어로 질문하면 Bedrock Claude로 응답.
DB 쿼리(TANGO/Safety) 결과도 포함하여 답변.

Endpoints:
  POST /api/v1/telegram/webhook  — Telegram webhook
  POST /api/v1/telegram/send     — Pod에서 사용자에게 메시지 발송
  GET  /api/v1/telegram/status   — 봇 상태 조회
"""

import re
import logging
from datetime import datetime, timezone

import httpx
import boto3
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import get_current_user
from app.core.database import get_db, Base

# 사번 패턴: N + 6~9자리 숫자 (예: N1102359)
SABUN_PATTERN = re.compile(r"^[Nn]\d{6,9}$")

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB Models
# ---------------------------------------------------------------------------

class TelegramMapping(Base):
    """텔레그램 ID <-> 사번 매핑 테이블."""
    __tablename__ = "telegram_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    telegram_name = Column(String(100))
    username = Column(String(50), nullable=False)  # 사번
    registered_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class TelegramChatLog(Base):
    """텔레그램 대화 감사 로그."""
    __tablename__ = "telegram_chat_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False)
    username = Column(String(50))
    message = Column(Text)
    response = Column(Text)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def send_telegram_message(chat_id: int, text: str, settings: Settings):
    """텔레그램으로 메시지 전송."""
    bot_token = settings.telegram_bot_token
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # 텔레그램 메시지 최대 4096자 — 안전하게 4000자에서 자름
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (메시지가 너무 길어 잘렸습니다)"

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })


async def call_bedrock_claude(
    message: str,
    username: str,
    settings: Settings,
) -> str:
    """Bedrock Claude API 호출."""
    try:
        bedrock = boto3.client(
            "bedrock-runtime",
            region_name=settings.bedrock_region,
        )

        system_prompt = (
            f"당신은 SKO 사내 AI 어시스턴트입니다. 한국어로 답변하세요.\n"
            f"사용자: {username}\n\n"
            "사용 가능한 데이터:\n"
            "- TANGO 알람 DB: 네트워크 실시간 고장 데이터 (psql-tango로 접근)\n"
            "- Safety DB: 안전관리시스템 데이터\n\n"
            "텔레그램에서 질문을 받고 있습니다. 간결하게 답변하세요.\n"
            "DB 쿼리가 필요한 질문이면 쿼리를 제안하세요."
        )

        response = bedrock.converse(
            modelId=settings.bedrock_sonnet_model,
            messages=[{
                "role": "user",
                "content": [{"text": message}],
            }],
            system=[{"text": system_prompt}],
            inferenceConfig={
                "maxTokens": 1024,
                "temperature": 0.3,
            },
        )

        output = response["output"]["message"]["content"][0]["text"]
        return output

    except Exception as e:
        logger.error(f"Bedrock error: {e}")
        return f"AI 응답 오류: {str(e)[:200]}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Telegram webhook — 메시지 수신 + 응답."""
    body = await request.json()

    message = body.get("message", {})
    if not message:
        return JSONResponse({"ok": True})

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    from_user = message.get("from", {})
    telegram_id = from_user.get("id")
    telegram_name = (
        from_user.get("first_name", "") + " " + from_user.get("last_name", "")
    )

    if not chat_id or not text:
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # /등록 명령어 처리 — 텔레그램 ID <-> 사번 매핑 (레거시 지원)
    # ------------------------------------------------------------------
    if text.startswith("/등록") or text.startswith("/register"):
        parts = text.split()
        if len(parts) < 2:
            await send_telegram_message(
                chat_id,
                "사용법: /등록 사번\n예: /등록 N1102359",
                settings,
            )
            return JSONResponse({"ok": True})

        sabun = parts[1].upper()

        existing = (
            db.query(TelegramMapping)
            .filter(TelegramMapping.telegram_id == telegram_id)
            .first()
        )
        if existing:
            existing.username = sabun
            existing.telegram_name = telegram_name.strip()
        else:
            mapping = TelegramMapping(
                telegram_id=telegram_id,
                telegram_name=telegram_name.strip(),
                username=sabun,
            )
            db.add(mapping)
        db.commit()

        await send_telegram_message(
            chat_id,
            f"등록 완료! {sabun}님, 이제 자유롭게 질문하세요.",
            settings,
        )
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # /start 명령어 — 봇 안내 메시지
    # ------------------------------------------------------------------
    if text == "/start":
        await send_telegram_message(
            chat_id,
            "*SKO Claude Assistant*\n\n"
            "사내 AI 어시스턴트입니다.\n\n"
            "사번을 입력하시면 바로 시작할 수 있습니다.\n"
            "(예: N1102359)\n\n"
            "*사용 예시:*\n"
            "- 현재 고장 현황 알려줘\n"
            "- 오늘 TBM 건수는?\n"
            "- 경남 팀별 알람 분석해줘",
            settings,
        )
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # 사번 매핑 확인 — 미등록 사용자는 자동 등록 유도
    # ------------------------------------------------------------------
    mapping = (
        db.query(TelegramMapping)
        .filter(TelegramMapping.telegram_id == telegram_id)
        .first()
    )
    if not mapping:
        # 사번 형태의 메시지면 자동 등록
        if SABUN_PATTERN.match(text):
            sabun = text.upper()
            new_mapping = TelegramMapping(
                telegram_id=telegram_id,
                telegram_name=telegram_name.strip(),
                username=sabun,
            )
            db.add(new_mapping)
            db.commit()

            await send_telegram_message(
                chat_id,
                f"등록 완료! {sabun}님, 이제 자유롭게 질문하세요.",
                settings,
            )
            return JSONResponse({"ok": True})

        # 사번이 아닌 일반 메시지 — 사번 입력 안내
        await send_telegram_message(
            chat_id,
            "안녕하세요! Claude Code 플랫폼 봇입니다.\n사번을 입력해주세요. (예: N1102359)",
            settings,
        )
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # /연장요청 — 사용자가 시간 연장을 요청
    # ------------------------------------------------------------------
    if text.startswith("/연장요청"):
        parts = text.split()
        hours = 2  # 기본 2시간
        if len(parts) >= 2:
            try:
                hours = int(parts[1])
                if hours < 1 or hours > 8:
                    hours = 2
            except ValueError:
                hours = 2

        from app.routers.scheduling import ExtensionRequest, ADMIN_USERNAME, _notify_admin_extension
        from app.models.user import User

        # 이미 대기 중인 요청 확인
        existing = (
            db.query(ExtensionRequest)
            .filter(
                ExtensionRequest.username == mapping.username,
                ExtensionRequest.status == "pending",
            )
            .first()
        )
        if existing:
            await send_telegram_message(
                chat_id,
                "이미 연장 요청이 대기 중입니다. 관리자 승인을 기다려주세요.",
                settings,
            )
            return JSONResponse({"ok": True})

        user = db.query(User).filter(User.username == mapping.username).first()
        user_name = user.name if user else mapping.username

        req = ExtensionRequest(
            username=mapping.username,
            user_name=user_name,
            requested_hours=hours,
        )
        db.add(req)
        db.commit()
        db.refresh(req)

        # 관리자에게 텔레그램 알림
        await _notify_admin_extension(
            mapping.username, user_name, hours, req.id, settings, db,
        )

        await send_telegram_message(
            chat_id,
            f"✅ {hours}시간 연장을 요청했습니다 (요청 #{req.id}).\n관리자 승인을 기다려주세요.",
            settings,
        )
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # /승인 {id} — 관리자가 연장 승인
    # ------------------------------------------------------------------
    if text.startswith("/승인"):
        from app.routers.scheduling import (
            ExtensionRequest, ADMIN_USERNAME,
            _notify_user_extension_result,
        )
        from datetime import timezone as _tz

        # 관리자 권한 확인
        if mapping.username != ADMIN_USERNAME:
            await send_telegram_message(
                chat_id, "관리자만 사용할 수 있는 명령입니다.", settings,
            )
            return JSONResponse({"ok": True})

        parts = text.split()
        if len(parts) < 2:
            await send_telegram_message(
                chat_id, "사용법: /승인 요청번호\n예: /승인 1", settings,
            )
            return JSONResponse({"ok": True})

        try:
            req_id = int(parts[1])
        except ValueError:
            await send_telegram_message(
                chat_id, "요청번호는 숫자여야 합니다.", settings,
            )
            return JSONResponse({"ok": True})

        req = db.query(ExtensionRequest).filter(ExtensionRequest.id == req_id).first()
        if not req:
            await send_telegram_message(
                chat_id, f"요청 #{req_id}을 찾을 수 없습니다.", settings,
            )
            return JSONResponse({"ok": True})

        if req.status != "pending":
            await send_telegram_message(
                chat_id, f"요청 #{req_id}은 이미 {req.status} 상태입니다.", settings,
            )
            return JSONResponse({"ok": True})

        req.status = "approved"
        req.resolved_at = datetime.now(_tz.utc)
        req.resolved_by = mapping.username
        db.commit()

        await _notify_user_extension_result(
            req.username, "approved", req.requested_hours, settings, db,
        )

        await send_telegram_message(
            chat_id,
            f"✅ 승인 완료 — {req.user_name}({req.username}) {req.requested_hours}시간 연장",
            settings,
        )
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # /거절 {id} — 관리자가 연장 거절
    # ------------------------------------------------------------------
    if text.startswith("/거절"):
        from app.routers.scheduling import (
            ExtensionRequest, ADMIN_USERNAME,
            _notify_user_extension_result,
        )
        from datetime import timezone as _tz

        if mapping.username != ADMIN_USERNAME:
            await send_telegram_message(
                chat_id, "관리자만 사용할 수 있는 명령입니다.", settings,
            )
            return JSONResponse({"ok": True})

        parts = text.split()
        if len(parts) < 2:
            await send_telegram_message(
                chat_id, "사용법: /거절 요청번호\n예: /거절 1", settings,
            )
            return JSONResponse({"ok": True})

        try:
            req_id = int(parts[1])
        except ValueError:
            await send_telegram_message(
                chat_id, "요청번호는 숫자여야 합니다.", settings,
            )
            return JSONResponse({"ok": True})

        req = db.query(ExtensionRequest).filter(ExtensionRequest.id == req_id).first()
        if not req:
            await send_telegram_message(
                chat_id, f"요청 #{req_id}을 찾을 수 없습니다.", settings,
            )
            return JSONResponse({"ok": True})

        if req.status != "pending":
            await send_telegram_message(
                chat_id, f"요청 #{req_id}은 이미 {req.status} 상태입니다.", settings,
            )
            return JSONResponse({"ok": True})

        req.status = "rejected"
        req.resolved_at = datetime.now(_tz.utc)
        req.resolved_by = mapping.username
        db.commit()

        await _notify_user_extension_result(
            req.username, "rejected", 0, settings, db,
        )

        await send_telegram_message(
            chat_id,
            f"❌ 거절 완료 — {req.user_name}({req.username}) 연장 요청 거절됨",
            settings,
        )
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # 단체방 멘션/답글 필터링
    # 개인 DM은 항상 응답, 단체방은 멘션 또는 reply 시에만
    # ------------------------------------------------------------------
    chat_type = message.get("chat", {}).get("type", "private")
    if chat_type in ("group", "supergroup"):
        bot_mentioned = False
        entities = message.get("entities", [])
        for entity in entities:
            if entity.get("type") == "mention":
                bot_mentioned = True
                break
        if not bot_mentioned and not message.get("reply_to_message"):
            return JSONResponse({"ok": True})
        # 멘션 텍스트에서 @봇이름 제거
        bot_username = settings.telegram_bot_username or ""
        text = text.replace("@" + bot_username, "").strip()

    # ------------------------------------------------------------------
    # Bedrock Claude 호출
    # ------------------------------------------------------------------
    await send_telegram_message(chat_id, "분석 중...", settings)

    response_text = await call_bedrock_claude(text, mapping.username, settings)

    # 감사 로그 저장
    log = TelegramChatLog(
        telegram_id=telegram_id,
        username=mapping.username,
        message=text,
        response=response_text[:2000],
    )
    db.add(log)
    db.commit()

    await send_telegram_message(chat_id, response_text, settings)
    return JSONResponse({"ok": True})


@router.post("/send")
async def send_telegram_to_user(
    request: dict,
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Pod에서 특정 사용자에게 텔레그램 메시지 발송.

    Body: {"username": "N1102359", "message": "hello"}
    인증: Bearer token (Pod 내 CLAUDE_TOKEN)
    """
    username = request.get("username", "").upper()
    message = request.get("message", "")

    if not username or not message:
        raise HTTPException(status_code=400, detail="username과 message를 입력하세요")

    # 수신자의 텔레그램 매핑 조회
    mapping = (
        db.query(TelegramMapping)
        .filter(TelegramMapping.username == username)
        .first()
    )
    if not mapping:
        raise HTTPException(
            status_code=404,
            detail=f"{username}이(가) 텔레그램에 등록되지 않았습니다",
        )

    # 텔레그램 메시지 발송
    await send_telegram_message(
        mapping.telegram_id,
        f"[Claude Code] {message}",
        settings,
    )

    return {"sent": True, "username": username}


@router.get("/status")
async def telegram_status(
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """텔레그램 봇 상태."""
    mappings = db.query(TelegramMapping).count()
    return {
        "bot_configured": bool(settings.telegram_bot_token),
        "registered_users": mappings,
    }
