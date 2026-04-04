"""사용자 봇 CRUD 테스트.

7 cases:
1. Register bot with valid token → success
2. Register bot with invalid token → 400
3. Register duplicate bot → 409
4. List bots → returns only user's bots
5. Get token by owner → returns decrypted token
6. Get token by non-owner → 403
7. Delete bot → removes from DB + calls deleteWebhook
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from app.models.bot import UserBot
from app.services.bot_crypto import BotCrypto


# ---------------------------------------------------------------------------
# Fernet key fixture — tests need a stable encryption key
# ---------------------------------------------------------------------------

FERNET_TEST_KEY = "dGVzdC1rZXktZm9yLWZlcm5ldC0xMjM0NTY3ODkwYWI="  # will generate real one

@pytest.fixture(autouse=True)
def _set_bot_encryption_key():
    """Set a test Fernet key for all bot tests."""
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    os.environ["BOT_ENCRYPTION_KEY"] = key
    yield
    os.environ.pop("BOT_ENCRYPTION_KEY", None)


# ---------------------------------------------------------------------------
# Mock Telegram API responses
# ---------------------------------------------------------------------------

def _mock_getme_ok(*args, **kwargs):
    """getMe 성공 응답."""
    return {"ok": True, "result": {"id": 12345, "username": "test_bot", "is_bot": True}}


def _mock_getme_fail(*args, **kwargs):
    """getMe 실패 응답 (잘못된 토큰)."""
    return {"ok": False, "error_code": 401, "description": "Unauthorized"}


def _mock_setwebhook_ok(*args, **kwargs):
    """setWebhook 성공 응답."""
    return {"ok": True, "result": True}


def _mock_deletewebhook_ok(*args, **kwargs):
    """deleteWebhook 성공 응답."""
    return {"ok": True, "result": True}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBotRegister:
    """POST /api/v1/bots/register"""

    @patch("app.routers.bots._telegram_api")
    def test_register_valid_token(self, mock_api, client, db_session):
        """1. 유효한 봇 토큰 → 등록 성공."""
        async def side_effect(token, method, payload=None):
            if method == "getMe":
                return _mock_getme_ok()
            if method == "setWebhook":
                return _mock_setwebhook_ok()
            return {"ok": True}

        mock_api.side_effect = side_effect

        resp = client.post("/api/v1/bots/register", json={
            "bot_token": "123456:ABC-DEF",
            "description": "My test bot",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["bot_username"] == "test_bot"
        assert data["status"] == "active"
        assert "id" in data

        # DB에 저장 확인
        bot = db_session.query(UserBot).filter(UserBot.id == data["id"]).first()
        assert bot is not None
        assert bot.user_id == "TESTUSER01"
        assert bot.bot_username == "test_bot"

    @patch("app.routers.bots._telegram_api")
    def test_register_invalid_token(self, mock_api, client):
        """2. 잘못된 봇 토큰 → 400."""
        mock_api.return_value = _mock_getme_fail()

        resp = client.post("/api/v1/bots/register", json={
            "bot_token": "invalid-token",
        })

        assert resp.status_code == 400
        assert "유효하지 않은" in resp.json()["detail"]

    @patch("app.routers.bots._telegram_api")
    def test_register_duplicate_token(self, mock_api, client, db_session):
        """3. 이미 등록된 봇 → 409."""
        async def side_effect(token, method, payload=None):
            if method == "getMe":
                return _mock_getme_ok()
            if method == "setWebhook":
                return _mock_setwebhook_ok()
            return {"ok": True}

        mock_api.side_effect = side_effect

        # 첫 번째 등록
        resp1 = client.post("/api/v1/bots/register", json={
            "bot_token": "123456:SAME-TOKEN",
        })
        assert resp1.status_code == 200

        # 같은 토큰으로 두 번째 등록 시도
        resp2 = client.post("/api/v1/bots/register", json={
            "bot_token": "123456:SAME-TOKEN",
        })
        assert resp2.status_code == 409
        assert "이미 등록된" in resp2.json()["detail"]


class TestBotList:
    """GET /api/v1/bots"""

    @patch("app.routers.bots._telegram_api")
    def test_list_own_bots(self, mock_api, client, db_session):
        """4. 봇 목록 — 본인의 봇만 반환."""
        async def side_effect(token, method, payload=None):
            if method == "getMe":
                return _mock_getme_ok()
            if method == "setWebhook":
                return _mock_setwebhook_ok()
            return {"ok": True}

        mock_api.side_effect = side_effect

        # 본인 봇 등록
        client.post("/api/v1/bots/register", json={"bot_token": "111:AAA"})

        # 다른 사용자의 봇 직접 DB 삽입
        crypto = BotCrypto()
        other_bot = UserBot(
            user_id="OTHER_USER",
            bot_token_encrypted=crypto.encrypt_token("999:ZZZ"),
            bot_token_hash=crypto.hash_token("999:ZZZ"),
            bot_username="other_bot",
            webhook_secret="other-secret",
            status="active",
        )
        db_session.add(other_bot)
        db_session.commit()

        # 목록 조회
        resp = client.get("/api/v1/bots")
        assert resp.status_code == 200
        bots = resp.json()
        assert len(bots) == 1
        assert bots[0]["bot_username"] == "test_bot"


class TestBotToken:
    """GET /api/v1/bots/{bot_id}/token"""

    @patch("app.routers.bots._telegram_api")
    def test_get_token_by_owner(self, mock_api, client, db_session):
        """5. 소유자가 토큰 조회 → 복호화된 토큰 반환."""
        async def side_effect(token, method, payload=None):
            if method == "getMe":
                return _mock_getme_ok()
            if method == "setWebhook":
                return _mock_setwebhook_ok()
            return {"ok": True}

        mock_api.side_effect = side_effect

        resp = client.post("/api/v1/bots/register", json={
            "bot_token": "555:MY-SECRET-TOKEN",
        })
        bot_id = resp.json()["id"]

        resp = client.get(f"/api/v1/bots/{bot_id}/token")
        assert resp.status_code == 200
        assert resp.json()["bot_token"] == "555:MY-SECRET-TOKEN"

    def test_get_token_by_non_owner(self, client, db_session):
        """6. 비소유자가 토큰 조회 → 403."""
        # 다른 사용자의 봇 직접 DB 삽입
        crypto = BotCrypto()
        other_bot = UserBot(
            user_id="OTHER_USER",
            bot_token_encrypted=crypto.encrypt_token("888:OTHER"),
            bot_token_hash=crypto.hash_token("888:OTHER"),
            bot_username="other_bot",
            webhook_secret="secret",
            status="active",
        )
        db_session.add(other_bot)
        db_session.commit()
        db_session.refresh(other_bot)

        resp = client.get(f"/api/v1/bots/{other_bot.id}/token")
        assert resp.status_code == 403


class TestBotDelete:
    """DELETE /api/v1/bots/{bot_id}"""

    @patch("app.routers.bots._telegram_api")
    def test_delete_bot(self, mock_api, client, db_session):
        """7. 봇 삭제 → DB에서 제거 + deleteWebhook 호출."""
        async def side_effect(token, method, payload=None):
            if method == "getMe":
                return _mock_getme_ok()
            if method == "setWebhook":
                return _mock_setwebhook_ok()
            if method == "deleteWebhook":
                return _mock_deletewebhook_ok()
            return {"ok": True}

        mock_api.side_effect = side_effect

        # 봇 등록
        resp = client.post("/api/v1/bots/register", json={
            "bot_token": "777:DELETE-ME",
        })
        bot_id = resp.json()["id"]

        # 삭제
        resp = client.delete(f"/api/v1/bots/{bot_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # DB에서 삭제 확인
        bot = db_session.query(UserBot).filter(UserBot.id == bot_id).first()
        assert bot is None

        # deleteWebhook이 호출되었는지 확인
        calls = [call for call in mock_api.call_args_list if "deleteWebhook" in str(call)]
        assert len(calls) >= 1
