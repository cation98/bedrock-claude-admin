"""사용자 텔레그램 봇 핸들러 (스타터 템플릿).

이 파일은 Pod 내부에서 실행되어 Gateway가 전달하는 webhook을 처리한다.

Contract:
  - 포트 8080에서 HTTP 서버 실행
  - POST /bot/webhook 엔드포인트 구현
  - Gateway가 Telegram의 원본 JSON을 그대로 전달함

사용법:
  1. 이 파일을 프로젝트 디렉토리에 복사
  2. handle_message() 함수에 원하는 로직 구현
  3. python handler.py 로 실행 (또는 uvicorn handler:app --port 8080)

환경변수:
  BOT_TOKEN — 봇 토큰 (플랫폼 API에서 조회하거나 직접 설정)
  HTTPS_PROXY — Pod의 프록시 설정 (자동 적용됨)
"""

import os
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot-handler")

app = FastAPI(title="User Bot Handler")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")


async def send_message(chat_id: int, text: str):
    """텔레그램으로 메시지 전송.

    HTTPS_PROXY 환경변수가 설정되어 있으면 자동으로 프록시를 통해 전송된다.
    """
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN 환경변수가 설정되지 않았습니다.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    if len(text) > 4000:
        text = text[:4000] + "\n\n... (메시지가 너무 길어 잘렸습니다)"

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
        })


async def handle_message(chat_id: int, text: str, from_user: dict):
    """메시지 처리 로직 — 여기에 원하는 봇 기능을 구현하세요.

    Args:
        chat_id: 텔레그램 채팅 ID (응답 전송용).
        text: 수신된 메시지 텍스트.
        from_user: 발신자 정보 (id, first_name, last_name 등).
    """
    # 예시: /start 명령어 처리
    if text == "/start":
        await send_message(chat_id, "안녕하세요! 봇이 준비되었습니다.")
        return

    # 예시: 에코 봇 (수신 메시지를 그대로 반환)
    await send_message(chat_id, f"받은 메시지: {text}")


@app.post("/bot/webhook")
async def webhook(request: Request):
    """Gateway가 전달하는 Telegram webhook 처리.

    Gateway가 Telegram의 원본 Update JSON을 그대로 전달한다.
    https://core.telegram.org/bots/api#update 참고.
    """
    try:
        body = await request.json()

        message = body.get("message", {})
        if not message:
            return JSONResponse({"ok": True})

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()
        from_user = message.get("from", {})

        if chat_id and text:
            await handle_message(chat_id, text, from_user)

        return JSONResponse({"ok": True})

    except Exception as e:
        logger.error("Webhook processing error: %s", e, exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/health")
async def health():
    """헬스체크."""
    return {"status": "ok", "service": "bot-handler"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
