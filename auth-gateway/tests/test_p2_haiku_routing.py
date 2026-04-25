"""P2 Haiku 분기 라우팅 단위 테스트.

_apply_model_tier(), _get_user_model_tier(), 관리자 model-tier API를 검증.
네트워크·Bedrock 호출 없이 순수 로직만 테스트.
"""

import pytest
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.user import User
from app.routers.bedrock_proxy import _apply_model_tier, _get_user_model_tier

# ── In-memory SQLite ─────────────────────────────────────────────────────────

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = sessionmaker(bind=_engine)

Base.metadata.create_all(_engine)


@pytest.fixture()
def db():
    session = _Session()
    yield session
    session.rollback()
    session.close()


def _make_settings(haiku_model: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"):
    s = MagicMock()
    s.bedrock_haiku_model = haiku_model
    return s


def _create_user(db, username: str, model_tier: str = "sonnet") -> User:
    u = User(
        username=username,
        name="테스트",
        role="user",
        is_active=True,
        is_approved=True,
        model_tier=model_tier,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ── _apply_model_tier 테스트 ──────────────────────────────────────────────────

class TestApplyModelTier:
    SONNET = "global.anthropic.claude-sonnet-4-6"
    HAIKU = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

    def test_sonnet_tier_passes_through(self):
        s = _make_settings()
        result = _apply_model_tier(self.SONNET, "sonnet", s)
        assert result == self.SONNET

    def test_haiku_tier_downgrades_sonnet(self):
        s = _make_settings()
        result = _apply_model_tier(self.SONNET, "haiku", s)
        assert result == self.HAIKU

    def test_haiku_tier_keeps_haiku_unchanged(self):
        s = _make_settings()
        result = _apply_model_tier(self.HAIKU, "haiku", s)
        assert result == self.HAIKU

    def test_auto_tier_passes_through(self):
        s = _make_settings()
        result = _apply_model_tier(self.SONNET, "auto", s)
        assert result == self.SONNET

    def test_unknown_tier_passes_through(self):
        s = _make_settings()
        result = _apply_model_tier(self.SONNET, "nonexistent", s)
        assert result == self.SONNET

    def test_haiku_tier_uses_settings_model(self):
        custom_haiku = "us.anthropic.claude-haiku-custom"
        s = _make_settings(haiku_model=custom_haiku)
        result = _apply_model_tier(self.SONNET, "haiku", s)
        assert result == custom_haiku

    def test_haiku_tier_fallback_when_settings_empty(self):
        s = _make_settings(haiku_model="")
        result = _apply_model_tier(self.SONNET, "haiku", s)
        assert "haiku" in result


# ── _get_user_model_tier 테스트 ───────────────────────────────────────────────

class TestGetUserModelTier:
    def test_returns_haiku_for_haiku_user(self, db):
        _create_user(db, "N1234567", model_tier="haiku")
        result = _get_user_model_tier(db, "N1234567")
        assert result == "haiku"

    def test_returns_sonnet_for_sonnet_user(self, db):
        _create_user(db, "N7654321", model_tier="sonnet")
        result = _get_user_model_tier(db, "N7654321")
        assert result == "sonnet"

    def test_username_uppercased(self, db):
        _create_user(db, "N1111111", model_tier="haiku")
        result = _get_user_model_tier(db, "n1111111")  # lowercase input
        assert result == "haiku"

    def test_returns_sonnet_for_unknown_user(self, db):
        result = _get_user_model_tier(db, "XXXXXXX")
        assert result == "sonnet"


# ── 관리자 model-tier PATCH API 테스트 ───────────────────────────────────────

class TestModelTierAdminEndpoint:
    """_VALID_MODEL_TIERS 상수 및 DB 업데이트 로직 검증."""

    def test_valid_tiers_set(self):
        from app.routers.admin import _VALID_MODEL_TIERS
        assert _VALID_MODEL_TIERS == {"sonnet", "haiku", "auto"}

    def test_invalid_tier_not_in_set(self):
        from app.routers.admin import _VALID_MODEL_TIERS
        assert "opus" not in _VALID_MODEL_TIERS
        assert "premium" not in _VALID_MODEL_TIERS

    def test_db_update_reflects_new_tier(self, db):
        u = _create_user(db, "N8888888", model_tier="sonnet")
        u.model_tier = "haiku"
        db.commit()
        db.refresh(u)
        assert u.model_tier == "haiku"
        assert _get_user_model_tier(db, "N8888888") == "haiku"
