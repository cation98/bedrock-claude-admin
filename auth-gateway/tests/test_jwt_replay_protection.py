"""T4 TDD — JWT jti replay 보호 + refresh revocation cascade 테스트.

테스트 플랜 기준:
  - blacklisted jti → verify_jwt 실패
  - 사용자 레벨 revoke (jti replay 감지 시 cascade)
  - 다른 사용자는 영향 없음
  - 만료된 블랙리스트 항목 자동 GC

Security 회귀 테스트 — 매 배포 CI에 포함되어야 한다.
"""

import os
import time

os.environ.setdefault(
    "ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-32-chars-min-xx"
)

import hashlib
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.config import Settings, get_settings
from app.models.user import User  # noqa: F401
from app.models.session import TerminalSession  # noqa: F401


# ─── Test DB ─────────────────────────────────────────────────────────────────

_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(bind=_test_engine)


def _test_settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len-xxxx",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=15,
        jwt_refresh_token_expire_hours=12,
        debug=False,
        onlyoffice_jwt_secret="test-onlyoffice-jwt-secret-32-chars-min-xx",
    )


def _build_test_app() -> FastAPI:
    from app.routers.jwt_auth import router as jwt_auth_router
    app = FastAPI(title="Replay Protection Test App")
    app.include_router(jwt_auth_router)
    return app


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _setup_tables():
    Base.metadata.create_all(bind=_test_engine)
    yield
    Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture(autouse=True)
def _clear_blacklist():
    from app.core.jwt_rs256 import reset_blacklist_for_testing
    reset_blacklist_for_testing()
    yield
    reset_blacklist_for_testing()


@pytest.fixture()
def db_session():
    s = _TestSessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(db_session):
    app = _build_test_app()

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = _test_settings

    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc
    app.dependency_overrides.clear()


def _make_user_and_session(db_session, username: str, pod_name: str, raw_token: str):
    user = User(username=username, name="Test", role="user", is_approved=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    ts = TerminalSession(
        user_id=user.id,
        username=username,
        pod_name=pod_name,
        pod_status="running",
        pod_token_hash=token_hash,
    )
    db_session.add(ts)
    db_session.commit()
    db_session.refresh(ts)
    return user, ts


# ─── jti blacklist 단위 테스트 ───────────────────────────────────────────────

class TestJtiBlacklist:
    """jwt_rs256 모듈의 블랙리스트 함수 직접 검증."""

    def test_fresh_token_is_not_blacklisted(self):
        """새로 발급된 refresh_token의 jti는 블랙리스트에 없어야 한다."""
        from app.core.jwt_rs256 import create_refresh_token, verify_jwt
        settings = _test_settings()
        token, jti = create_refresh_token(
            "1", "N0000001", "n0000001@skons.net", "user", settings
        )
        payload = verify_jwt(token, expected_type="refresh")
        assert payload["jti"] == jti

    def test_blacklisted_jti_causes_verify_failure(self):
        """블랙리스트 등록 후 verify_jwt는 JWTError를 발생시킨다."""
        from jose import JWTError
        from app.core.jwt_rs256 import (
            create_refresh_token,
            verify_jwt,
            blacklist_jti,
        )
        settings = _test_settings()
        token, jti = create_refresh_token(
            "1", "N0000001", "n0000001@skons.net", "user", settings
        )
        blacklist_jti(jti, ttl_seconds=3600)
        with pytest.raises(JWTError):
            verify_jwt(token, expected_type="refresh")

    def test_blacklist_does_not_affect_other_jtis(self):
        """한 jti 블랙리스트가 다른 jti에 영향을 주지 않는다."""
        from app.core.jwt_rs256 import (
            create_refresh_token,
            verify_jwt,
            blacklist_jti,
        )
        settings = _test_settings()
        token1, jti1 = create_refresh_token(
            "1", "N0000001", "n0000001@skons.net", "user", settings
        )
        token2, jti2 = create_refresh_token(
            "2", "N0000002", "n0000002@skons.net", "user", settings
        )
        blacklist_jti(jti1, ttl_seconds=3600)

        # jti2는 영향 없음
        payload2 = verify_jwt(token2, expected_type="refresh")
        assert payload2["jti"] == jti2

    def test_expired_blacklist_entry_is_cleaned(self):
        """만료된 블랙리스트 항목은 체크 시 False 반환 + 자동 제거된다."""
        from app.core.jwt_rs256 import (
            _blacklist,
            _blacklist_lock,
            _blacklist_check,
        )
        key = "test-expired-key-xyz"
        with _blacklist_lock:
            _blacklist[key] = time.time() - 1  # 이미 만료

        assert not _blacklist_check(key)
        with _blacklist_lock:
            assert key not in _blacklist  # 자동 제거 확인


# ─── 사용자 레벨 revoke 단위 테스트 ─────────────────────────────────────────

class TestUserRevocation:
    def test_revoke_all_sets_user_revoked_flag(self):
        """revoke_all_refresh_for_user 호출 후 is_user_revoked는 True를 반환한다."""
        from app.core.jwt_rs256 import revoke_all_refresh_for_user, is_user_revoked
        revoke_all_refresh_for_user("user-abc-123", ttl_seconds=3600)
        assert is_user_revoked("user-abc-123")

    def test_revoke_does_not_affect_other_users(self):
        """한 사용자 revoke는 다른 사용자에게 영향을 주지 않는다."""
        from app.core.jwt_rs256 import revoke_all_refresh_for_user, is_user_revoked
        revoke_all_refresh_for_user("user-abc-123", ttl_seconds=3600)
        assert not is_user_revoked("user-xyz-999")


# ─── jti replay → cascade revoke (API 수준) ─────────────────────────────────

class TestJtiReplayCascade:
    """refresh endpoint에서 replay 감지 시 사용자 전체 세션 revoke 동작 검증."""

    def _setup_tokens(self, client, db_session, username="N1102359",
                      pod_name="claude-terminal-n1102359",
                      raw="pod-secret-99"):
        _make_user_and_session(db_session, username, pod_name, raw)
        resp = client.post(
            "/auth/pod-token-exchange",
            json={"pod_token": raw, "pod_name": pod_name},
        )
        assert resp.status_code == 200
        return resp.json()

    def test_refresh_replay_returns_401(self, client, db_session):
        """동일 refresh_token을 두 번 사용하면 두 번째는 401을 반환한다."""
        tokens = self._setup_tokens(client, db_session)
        refresh_token = tokens["refresh_token"]

        # 1회 사용
        resp1 = client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert resp1.status_code == 200

        # 2회 사용 (replay 시도)
        resp2 = client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert resp2.status_code == 401

    def test_refresh_replay_triggers_user_revocation(self, client, db_session):
        """replay 감지 시 해당 사용자의 모든 refresh가 revoke 된다.

        새로 발급받은 refresh_token도 replay 감지 이후 사용 불가.
        """
        tokens = self._setup_tokens(client, db_session)
        refresh_token = tokens["refresh_token"]

        # 1회 사용 → 새 access_token + jti rotate
        resp1 = client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert resp1.status_code == 200

        # 원본 refresh_token replay → 이 시점에서 user revoke cascade
        client.post("/auth/refresh", json={"refresh_token": refresh_token})

        # sub 추출: 블랙리스트된 토큰에서 서명 검증 없이 claim 읽기
        # (replay 이후 jti가 블랙리스트에 있으므로 verify_jwt는 실패 — get_unverified_claims 사용)
        from jose import jwt as jose_jwt
        from app.core.jwt_rs256 import is_user_revoked

        unverified = jose_jwt.get_unverified_claims(refresh_token)
        sub = unverified["sub"]

        # 사용자 레벨 revoke 확인
        assert is_user_revoked(sub)
