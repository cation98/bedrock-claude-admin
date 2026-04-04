"""사용자별 텔레그램 봇 관리 API.

사용자가 자신의 텔레그램 봇을 등록하면:
1. Gateway가 Telegram setWebhook을 호출하여 webhook URL을 설정
2. 텔레그램이 메시지를 보내면 Gateway가 받아서 사용자 Pod로 전달
3. Pod 내부의 봇 핸들러(handler.py)가 메시지를 처리

Endpoints:
  POST   /api/v1/bots/register              — 봇 등록
  POST   /api/v1/telegram/bot/{hash}/webhook — 텔레그램 webhook 수신 (Pod로 전달)
  GET    /api/v1/bots                        — 내 봇 목록
  GET    /api/v1/bots/{bot_id}/token         — 봇 토큰 조회 (본인만)
  DELETE /api/v1/bots/{bot_id}               — 봇 삭제
"""

import hmac
import logging
import secrets
from functools import lru_cache

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.bot import UserBot
from app.models.session import TerminalSession
from app.services.bot_crypto import BotCrypto, get_cached_token

router = APIRouter(prefix="/api/v1", tags=["bots"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class BotRegisterRequest(BaseModel):
    bot_token: str
    description: str = ""


class BotResponse(BaseModel):
    id: int
    bot_username: str | None
    status: str
    description: str | None
    created_at: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_crypto() -> BotCrypto:
    """BotCrypto 싱글턴 (BOT_ENCRYPTION_KEY 환경변수 사용)."""
    return BotCrypto()


async def _telegram_api(bot_token: str, method: str, payload: dict | None = None) -> dict:
    """Telegram Bot API 호출 헬퍼.

    Args:
        bot_token: 봇 토큰 (평문).
        method: API 메서드 (e.g., "getMe", "setWebhook", "deleteWebhook").
        payload: POST body (optional).

    Returns:
        Telegram API 응답 JSON.
    """
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        if payload:
            resp = await client.post(url, json=payload)
        else:
            resp = await client.post(url)
        return resp.json()


async def _send_bot_message(bot_token: str, chat_id: int, text: str):
    """사용자 봇으로 텔레그램 메시지 전송."""
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (메시지가 너무 길어 잘렸습니다)"

    await _telegram_api(bot_token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
    })


# ---------------------------------------------------------------------------
# POST /api/v1/bots/register — 봇 등록
# ---------------------------------------------------------------------------

@router.post("/bots/register")
async def register_bot(
    body: BotRegisterRequest,
    current_user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """사용자의 텔레그램 봇을 플랫폼에 등록.

    1. Telegram getMe로 토큰 유효성 검증
    2. SHA-256 해시로 중복 확인
    3. Fernet 암호화 후 DB 저장
    4. Telegram setWebhook 호출 (webhook URL + secret_token 설정)
    """
    username = current_user.get("sub", "")
    bot_token = body.bot_token.strip()

    if not bot_token:
        raise HTTPException(status_code=400, detail="bot_token은 필수입니다.")

    # 1) Telegram getMe — 토큰 유효성 확인
    me_result = await _telegram_api(bot_token, "getMe")
    if not me_result.get("ok"):
        raise HTTPException(status_code=400, detail="유효하지 않은 봇 토큰입니다.")

    bot_info = me_result.get("result", {})
    bot_username = bot_info.get("username", "")

    # 2) 해시 중복 확인
    crypto = _get_crypto()
    token_hash = crypto.hash_token(bot_token)

    existing = db.query(UserBot).filter(UserBot.bot_token_hash == token_hash).first()
    if existing:
        raise HTTPException(status_code=409, detail="이미 등록된 봇입니다.")

    # 3) 암호화 + DB 저장
    encrypted = crypto.encrypt_token(bot_token)
    webhook_secret = secrets.token_hex(32)

    bot = UserBot(
        user_id=username,
        bot_token_encrypted=encrypted,
        bot_token_hash=token_hash,
        bot_username=bot_username,
        webhook_secret=webhook_secret,
        status="active",
        description=body.description or "",
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)

    # 4) Telegram setWebhook
    external_host = settings.external_host or "claude.skons.net"
    webhook_url = f"https://{external_host}/api/v1/telegram/bot/{token_hash}/webhook"

    webhook_result = await _telegram_api(bot_token, "setWebhook", {
        "url": webhook_url,
        "secret_token": webhook_secret,
    })

    if not webhook_result.get("ok"):
        # webhook 설정 실패 — 봇을 error 상태로 변경
        bot.status = "error"
        db.commit()
        logger.error("setWebhook failed for bot %s: %s", bot_username, webhook_result)

    return {
        "id": bot.id,
        "bot_username": bot.bot_username,
        "status": bot.status,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/telegram/bot/{token_hash}/webhook — 텔레그램 webhook 수신
# ---------------------------------------------------------------------------

@router.post("/telegram/bot/{token_hash}/webhook")
async def user_bot_webhook(
    token_hash: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """텔레그램이 사용자 봇으로 보낸 메시지를 수신하여 Pod로 전달.

    흐름:
      Telegram → Gateway (이 엔드포인트) → 사용자 Pod (port 8080 /bot/webhook)

    보안:
      - X-Telegram-Bot-Api-Secret-Token 헤더로 webhook_secret 검증
      - 미등록 해시 → 404
      - secret 불일치 → 403
    """
    # 1) 봇 조회
    bot = db.query(UserBot).filter(UserBot.bot_token_hash == token_hash).first()
    if not bot:
        raise HTTPException(status_code=404, detail="등록되지 않은 봇입니다.")
    if bot.status != "active":
        # paused/error 봇: Telegram에 200 반환하여 webhook 유지, 메시지는 무시
        return JSONResponse(content={"ok": True})

    # 2) Telegram secret_token 검증
    telegram_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(telegram_secret, bot.webhook_secret):
        raise HTTPException(status_code=403, detail="유효하지 않은 webhook secret입니다.")

    # 3) 사용자 Pod 조회
    session = db.query(TerminalSession).filter(
        TerminalSession.username == bot.user_id,
        TerminalSession.pod_status == "running",
    ).first()

    body = await request.body()

    # chat_id 추출 (오프라인 메시지 전송용)
    try:
        import json
        body_json = json.loads(body)
        chat_id = (
            body_json.get("message", {}).get("chat", {}).get("id")
            or body_json.get("callback_query", {}).get("message", {}).get("chat", {}).get("id")
        )
    except Exception:
        chat_id = None

    # 4) Pod가 없거나 running이 아닌 경우 — 오프라인 메시지 전송
    if not session or not session.pod_name:
        if chat_id:
            try:
                crypto = _get_crypto()
                plain_token = get_cached_token(bot.bot_token_encrypted, crypto)
                await _send_bot_message(
                    plain_token, chat_id,
                    "봇 주인의 환경이 오프라인입니다. 잠시 후 다시 시도해주세요.",
                )
            except Exception as e:
                logger.error("Failed to send offline message: %s", e)
        return JSONResponse({"ok": True})

    # 5) Pod로 전달
    pod_url = f"http://{session.pod_name}.claude-sessions:8080/bot/webhook"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                pod_url,
                content=body,
                headers={"Content-Type": "application/json"},
            )

        if resp.status_code >= 500:
            # Pod 내부 오류 — 사용자에게 알림
            if chat_id:
                try:
                    crypto = _get_crypto()
                    plain_token = get_cached_token(bot.bot_token_encrypted, crypto)
                    await _send_bot_message(
                        plain_token, chat_id,
                        "봇 처리 중 오류가 발생했습니다.",
                    )
                except Exception as e:
                    logger.error("Failed to send error message: %s", e)

    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("Pod %s unreachable: %s", session.pod_name, e)
        if chat_id:
            try:
                crypto = _get_crypto()
                plain_token = get_cached_token(bot.bot_token_encrypted, crypto)
                await _send_bot_message(
                    plain_token, chat_id,
                    "봇 주인의 환경이 오프라인입니다. 잠시 후 다시 시도해주세요.",
                )
            except Exception as exc:
                logger.error("Failed to send offline message: %s", exc)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# GET /api/v1/bots — 내 봇 목록
# ---------------------------------------------------------------------------

@router.get("/bots")
async def list_bots(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """현재 사용자의 등록된 봇 목록 조회."""
    username = current_user.get("sub", "")
    bots = db.query(UserBot).filter(UserBot.user_id == username).all()
    return [
        {
            "id": b.id,
            "bot_username": b.bot_username,
            "status": b.status,
            "description": b.description,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in bots
    ]


# ---------------------------------------------------------------------------
# GET /api/v1/bots/{bot_id}/token — 봇 토큰 조회 (본인만)
# ---------------------------------------------------------------------------

@router.get("/bots/{bot_id}/token")
async def get_bot_token(
    bot_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """봇 토큰 복호화 반환 (봇 소유자만 가능)."""
    username = current_user.get("sub", "")

    bot = db.query(UserBot).filter(UserBot.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=404, detail="봇을 찾을 수 없습니다.")

    if bot.user_id != username:
        raise HTTPException(status_code=403, detail="본인의 봇만 조회할 수 있습니다.")

    crypto = _get_crypto()
    plain_token = crypto.decrypt_token(bot.bot_token_encrypted)

    # Log token access for security audit
    logger.warning(f"Bot token accessed: bot_id={bot_id}, user={current_user.get('username')}")

    return {"bot_token": plain_token}


# ---------------------------------------------------------------------------
# DELETE /api/v1/bots/{bot_id} — 봇 삭제
# ---------------------------------------------------------------------------

@router.delete("/bots/{bot_id}")
async def delete_bot(
    bot_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """봇 삭제 (Telegram deleteWebhook 호출 후 DB 삭제)."""
    username = current_user.get("sub", "")

    bot = db.query(UserBot).filter(UserBot.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=404, detail="봇을 찾을 수 없습니다.")

    if bot.user_id != username:
        raise HTTPException(status_code=403, detail="본인의 봇만 삭제할 수 있습니다.")

    # Telegram deleteWebhook 호출
    try:
        crypto = _get_crypto()
        plain_token = crypto.decrypt_token(bot.bot_token_encrypted)
        await _telegram_api(plain_token, "deleteWebhook")
    except Exception as e:
        logger.warning("deleteWebhook failed for bot %d: %s", bot_id, e)

    db.delete(bot)
    db.commit()
    return {"deleted": True, "bot_id": bot_id}
