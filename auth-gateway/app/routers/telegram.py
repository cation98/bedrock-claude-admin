"""텔레그램 봇 API.

임원이 텔레그램에서 자연어로 질문하면 Bedrock Claude로 응답.
DB 쿼리(TANGO/Safety) 결과도 포함하여 답변.

Endpoints:
  POST /api/v1/telegram/webhook  — Telegram webhook
  POST /api/v1/telegram/register — 사번 등록 (텔레그램 ID <-> 사번 매핑)
  GET  /api/v1/telegram/status   — 봇 상태 조회
"""

import logging
from datetime import datetime, timezone

import httpx
import boto3
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db, Base

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
    # /등록 명령어 처리 — 텔레그램 ID <-> 사번 매핑
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
            f"등록 완료!\n사번: {sabun}\n이제 자유롭게 질문하세요.",
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
            "*시작하기:*\n"
            "`/등록 사번` (예: /등록 N1102359)\n\n"
            "*사용 예시:*\n"
            "- 현재 고장 현황 알려줘\n"
            "- 오늘 TBM 건수는?\n"
            "- 경남 팀별 알람 분석해줘",
            settings,
        )
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # 사번 매핑 확인 — 미등록 사용자 차단
    # ------------------------------------------------------------------
    mapping = (
        db.query(TelegramMapping)
        .filter(TelegramMapping.telegram_id == telegram_id)
        .first()
    )
    if not mapping:
        await send_telegram_message(
            chat_id,
            "먼저 등록이 필요합니다.\n`/등록 사번` 을 입력해주세요.\n예: `/등록 N1102359`",
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
