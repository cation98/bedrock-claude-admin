"""사용자 봇 webhook 테스트.

5 cases:
1. Valid webhook → forward to Pod → 200
2. Unknown hash → 404
3. Pod offline → sends Telegram error message
4. Pod timeout → sends Telegram error message
5. Invalid secret_token header → 403
"""

import json
import os
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

from app.models.bot import UserBot
from app.models.session import TerminalSession
from app.models.user import User
from app.services.bot_crypto import BotCrypto


# ---------------------------------------------------------------------------
# Fernet key fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _set_bot_encryption_key():
    """Set a test Fernet key for all webhook tests."""
    from cryptography.fernet import Fernet
    from app.routers.bots import _get_crypto
    from app.services.bot_crypto import _decrypt_cache

    key = Fernet.generate_key().decode()
    os.environ["BOT_ENCRYPTION_KEY"] = key
    # Clear singleton and decrypt caches so each test gets a fresh BotCrypto
    _get_crypto.cache_clear()
    _decrypt_cache.clear()
    yield
    os.environ.pop("BOT_ENCRYPTION_KEY", None)
    _get_crypto.cache_clear()
    _decrypt_cache.clear()


# ---------------------------------------------------------------------------
# Helper: create a bot + session in DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def bot_and_session(db_session):
    """봇 + 실행 중인 세션을 DB에 삽입."""
    # TerminalSession은 users.id FK가 있으므로 User 먼저 생성
    user = User(username="TESTUSER01", name="Test User", role="user", is_approved=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    crypto = BotCrypto()
    token = "123456:TEST-BOT-TOKEN"

    bot = UserBot(
        user_id="TESTUSER01",
        bot_token_encrypted=crypto.encrypt_token(token),
        bot_token_hash=crypto.hash_token(token),
        bot_username="test_bot",
        webhook_secret="valid-webhook-secret-hex",
        status="active",
    )
    db_session.add(bot)

    session = TerminalSession(
        user_id=user.id,
        username="TESTUSER01",
        pod_name="claude-terminal-testuser01",
        pod_status="running",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(bot)

    return {
        "bot": bot,
        "session": session,
        "token": token,
        "token_hash": crypto.hash_token(token),
    }


@pytest.fixture()
def bot_without_session(db_session):
    """봇만 있고 실행 중인 세션이 없는 상태."""
    crypto = BotCrypto()
    token = "654321:OFFLINE-BOT"

    bot = UserBot(
        user_id="OFFLINE_USER",
        bot_token_encrypted=crypto.encrypt_token(token),
        bot_token_hash=crypto.hash_token(token),
        bot_username="offline_bot",
        webhook_secret="offline-secret",
        status="active",
    )
    db_session.add(bot)
    db_session.commit()
    db_session.refresh(bot)

    return {
        "bot": bot,
        "token": token,
        "token_hash": crypto.hash_token(token),
    }


def _webhook_body(text: str = "hello", chat_id: int = 99999):
    """Telegram Update JSON 생성."""
    return {
        "update_id": 100,
        "message": {
            "message_id": 1,
            "from": {"id": 11111, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWebhookForward:
    """POST /api/v1/telegram/bot/{hash}/webhook"""

    @patch("app.routers.bots.httpx.AsyncClient")
    @patch("app.routers.bots._send_bot_message")
    def test_valid_webhook_forward(self, mock_send, mock_client_cls, client, bot_and_session):
        """1. 유효한 webhook → Pod로 전달 → 200."""
        token_hash = bot_and_session["token_hash"]

        # httpx.AsyncClient mock — Pod 응답 200
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        resp = client.post(
            f"/api/v1/telegram/bot/{token_hash}/webhook",
            json=_webhook_body(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "valid-webhook-secret-hex"},
        )

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_unknown_hash_404(self, client):
        """2. 미등록 해시 → 404."""
        resp = client.post(
            "/api/v1/telegram/bot/unknown-hash-value/webhook",
            json=_webhook_body(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "anything"},
        )

        assert resp.status_code == 404

    @patch("app.routers.bots._send_bot_message")
    def test_pod_offline_sends_message(self, mock_send, client, bot_without_session):
        """3. Pod 오프라인 → 텔레그램 오프라인 메시지 전송."""
        mock_send.return_value = None

        token_hash = bot_without_session["token_hash"]

        resp = client.post(
            f"/api/v1/telegram/bot/{token_hash}/webhook",
            json=_webhook_body(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "offline-secret"},
        )

        assert resp.status_code == 200
        # 오프라인 메시지가 전송되었는지 확인
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert "오프라인" in call_args[0][2]  # text argument

    @patch("app.routers.bots._send_bot_message")
    @patch("app.routers.bots.httpx.AsyncClient")
    def test_pod_timeout_sends_message(self, mock_client_cls, mock_send, client, bot_and_session):
        """4. Pod 타임아웃 → 텔레그램 오프라인 메시지 전송."""
        mock_send.return_value = None

        token_hash = bot_and_session["token_hash"]

        # httpx.AsyncClient mock — 타임아웃 발생
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        resp = client.post(
            f"/api/v1/telegram/bot/{token_hash}/webhook",
            json=_webhook_body(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "valid-webhook-secret-hex"},
        )

        assert resp.status_code == 200
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert "오프라인" in call_args[0][2]

    def test_invalid_secret_403(self, client, bot_and_session):
        """5. 잘못된 secret_token → 403."""
        token_hash = bot_and_session["token_hash"]

        resp = client.post(
            f"/api/v1/telegram/bot/{token_hash}/webhook",
            json=_webhook_body(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )

        assert resp.status_code == 403

    def test_missing_secret_header_403(self, client, bot_and_session):
        """6. X-Telegram-Bot-Api-Secret-Token 헤더 없이 요청 시 403."""
        resp = client.post(
            f"/api/v1/telegram/bot/{bot_and_session['token_hash']}/webhook",
            json={"update_id": 999, "message": {"message_id": 1, "date": 1234567890, "chat": {"id": 123, "type": "private"}, "text": "/start"}},
            # No X-Telegram-Bot-Api-Secret-Token header
        )
        assert resp.status_code == 403
