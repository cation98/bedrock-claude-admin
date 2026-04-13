"""UI Split 집계 API 테스트 (GET /api/v1/sessions/ui-source/stats).

weekly/monthly 버킷 집계 + summary + admin 인가 + 파라미터 검증을 검증.
자체 SQLite 인메모리 엔진 + 세션 라우터로 독립 실행.
"""

import os

# Settings에서 필수인 ONLYOFFICE_JWT_SECRET 주입
os.environ.setdefault(
    "ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-32-chars-min-xx"
)

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.core.security import get_current_user

# Base.metadata에 모든 필요 테이블 등록
from app.models.session import TerminalSession  # noqa: F401
from app.models.ui_source_event import UiSourceEvent

# 세션 라우터 임포트
from app.routers import sessions as sessions_router


# ── SQLite 인메모리 테스트 DB ────────────────────────────────────────────────

_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_test_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, conn_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_TestSession = sessionmaker(bind=_test_engine)

# ── 테스트 앱 ────────────────────────────────────────────────────────────────

_test_app = FastAPI(title="Test UI Source Stats")
_test_app.include_router(sessions_router.router)

# ── 공통 유저 ────────────────────────────────────────────────────────────────

_ADMIN_USER = {"sub": "ADMIN01", "role": "admin", "name": "Admin"}
_REGULAR_USER = {"sub": "USER01", "role": "user", "name": "User"}


def _test_settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-256-bit-minimum-len",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        debug=False,
        onlyoffice_jwt_secret="test-onlyoffice-jwt-secret-32-chars-min-xx",
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _setup_tables():
    Base.metadata.create_all(bind=_test_engine)
    yield
    Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture()
def db():
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def admin_client(db):
    def _override_db():
        yield db

    _test_app.dependency_overrides[get_db] = _override_db
    _test_app.dependency_overrides[get_settings] = _test_settings
    _test_app.dependency_overrides[get_current_user] = lambda: _ADMIN_USER.copy()

    with TestClient(_test_app, raise_server_exceptions=False) as tc:
        yield tc

    _test_app.dependency_overrides.clear()


@pytest.fixture()
def user_client(db):
    def _override_db():
        yield db

    _test_app.dependency_overrides[get_db] = _override_db
    _test_app.dependency_overrides[get_settings] = _test_settings
    _test_app.dependency_overrides[get_current_user] = lambda: _REGULAR_USER.copy()

    with TestClient(_test_app, raise_server_exceptions=False) as tc:
        yield tc

    _test_app.dependency_overrides.clear()


# ── 날짜 헬퍼 ────────────────────────────────────────────────────────────────

def _this_monday_naive() -> datetime:
    """이번 주 월요일 00:00 naive UTC."""
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )


def _this_month_first_naive() -> datetime:
    """이번 달 1일 00:00 naive UTC."""
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


# ── Tests: 기본 동작 ─────────────────────────────────────────────────────────

class TestUiSourceStatsBasic:

    def test_empty_db_returns_zero_counts(self, admin_client):
        """빈 데이터 → 모든 수 0, buckets 개수 = window."""
        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=weekly&window=4")
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "weekly"
        assert data["window"] == 4
        assert len(data["buckets"]) == 4
        assert data["webchat_total_users"] == 0
        assert data["console_total_users"] == 0
        assert data["both_users"] == 0
        assert data["webchat_only_users"] == 0
        assert data["console_only_users"] == 0
        for bucket in data["buckets"]:
            assert bucket["webchat_users"] == 0
            assert bucket["console_users"] == 0
            assert bucket["total_events"] == 0

    def test_default_params_returns_8_buckets(self, admin_client):
        """기본값 period=weekly, window=8 → 8개 버킷."""
        resp = admin_client.get("/api/v1/sessions/ui-source/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "weekly"
        assert data["window"] == 8
        assert len(data["buckets"]) == 8

    def test_window_param_respected(self, admin_client):
        """window 파라미터 값이 반환 버킷 수에 반영된다."""
        for w in (1, 4, 12, 52):
            resp = admin_client.get(f"/api/v1/sessions/ui-source/stats?window={w}")
            assert resp.status_code == 200, f"window={w} should succeed"
            assert len(resp.json()["buckets"]) == w


# ── Tests: Weekly 버킷 ───────────────────────────────────────────────────────

class TestUiSourceStatsWeekly:

    def test_webchat_events_placed_in_correct_bucket(self, admin_client, db):
        """2주 전 webchat 이벤트 2명 → 해당 버킷 webchat_users=2."""
        monday = _this_monday_naive()
        # 2주 전 버킷: [monday - 2w, monday - 1w)
        two_weeks_ago = monday - timedelta(weeks=2) + timedelta(hours=12)
        db.add(UiSourceEvent(username="USER_A", source="webchat", recorded_at=two_weeks_ago))
        db.add(UiSourceEvent(username="USER_B", source="webchat", recorded_at=two_weeks_ago))
        db.commit()

        # window=4: buckets[0]=monday-3w, [1]=monday-2w, [2]=monday-1w, [3]=current
        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=weekly&window=4")
        assert resp.status_code == 200
        buckets = resp.json()["buckets"]
        assert len(buckets) == 4
        two_weeks_bucket = buckets[1]  # index 1 = [monday-2w, monday-1w)
        assert two_weeks_bucket["webchat_users"] == 2
        assert two_weeks_bucket["console_users"] == 0
        assert two_weeks_bucket["total_events"] == 2
        # 다른 버킷은 0
        for i, b in enumerate(buckets):
            if i != 1:
                assert b["total_events"] == 0

    def test_distinct_count_on_repeated_events(self, admin_client, db):
        """같은 사용자가 여러 이벤트 → distinct 1로 집계."""
        monday = _this_monday_naive()
        last_week = monday - timedelta(weeks=1) + timedelta(hours=6)
        for _ in range(5):
            db.add(UiSourceEvent(username="REPEAT_USER", source="webchat", recorded_at=last_week))
        db.commit()

        # window=2: buckets[0]=[monday-1w, monday), buckets[1]=current
        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=weekly&window=2")
        assert resp.status_code == 200
        last_week_bucket = resp.json()["buckets"][0]
        assert last_week_bucket["webchat_users"] == 1   # distinct
        assert last_week_bucket["total_events"] == 5    # raw count

    def test_bucket_order_oldest_to_newest(self, admin_client):
        """버킷 순서: period_start 기준 오래된 것 → 최신."""
        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=weekly&window=5")
        assert resp.status_code == 200
        starts = [b["period_start"] for b in resp.json()["buckets"]]
        assert starts == sorted(starts)

    def test_event_outside_window_not_counted(self, admin_client, db):
        """window 범위 밖 이벤트는 집계에 포함되지 않는다."""
        monday = _this_monday_naive()
        very_old = monday - timedelta(weeks=10)
        db.add(UiSourceEvent(username="OLD_USER", source="webchat", recorded_at=very_old))
        db.commit()

        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=weekly&window=4")
        assert resp.status_code == 200
        data = resp.json()
        assert data["webchat_total_users"] == 0
        for b in data["buckets"]:
            assert b["webchat_users"] == 0


# ── Tests: Summary 집계 ──────────────────────────────────────────────────────

class TestUiSourceStatsSummary:

    def test_both_users_split_correctly(self, admin_client, db):
        """webchat+console 모두 사용 / 각각만 사용 → summary 정확히 분류."""
        monday = _this_monday_naive()
        last_week = monday - timedelta(weeks=1) + timedelta(hours=1)
        # USER_BOTH: 두 소스 모두 사용
        db.add(UiSourceEvent(username="USER_BOTH", source="webchat", recorded_at=last_week))
        db.add(UiSourceEvent(username="USER_BOTH", source="console", recorded_at=last_week))
        # USER_WEBCHAT: webchat만
        db.add(UiSourceEvent(username="USER_WEBCHAT", source="webchat", recorded_at=last_week))
        # USER_CONSOLE: console만
        db.add(UiSourceEvent(username="USER_CONSOLE", source="console", recorded_at=last_week))
        db.commit()

        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=weekly&window=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["webchat_total_users"] == 2   # USER_BOTH + USER_WEBCHAT
        assert data["console_total_users"] == 2   # USER_BOTH + USER_CONSOLE
        assert data["both_users"] == 1             # USER_BOTH
        assert data["webchat_only_users"] == 1     # USER_WEBCHAT
        assert data["console_only_users"] == 1     # USER_CONSOLE

    def test_summary_spans_multiple_buckets(self, admin_client, db):
        """여러 버킷에 걸친 distinct 사용자 → summary에 합산."""
        monday = _this_monday_naive()
        # 2주 전 webchat
        db.add(UiSourceEvent(
            username="ANCIENT_USER", source="webchat",
            recorded_at=monday - timedelta(weeks=2) + timedelta(hours=1),
        ))
        # 1주 전 console
        db.add(UiSourceEvent(
            username="RECENT_USER", source="console",
            recorded_at=monday - timedelta(weeks=1) + timedelta(hours=1),
        ))
        db.commit()

        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=weekly&window=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["webchat_total_users"] == 1
        assert data["console_total_users"] == 1
        assert data["both_users"] == 0
        assert data["webchat_only_users"] == 1
        assert data["console_only_users"] == 1


# ── Tests: Monthly ───────────────────────────────────────────────────────────

class TestUiSourceStatsMonthly:

    def test_monthly_returns_correct_window(self, admin_client):
        """monthly period → 버킷 수 = window."""
        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=monthly&window=6")
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "monthly"
        assert len(data["buckets"]) == 6

    def test_monthly_period_start_is_first_of_month(self, admin_client):
        """monthly 버킷 period_start는 항상 달의 1일."""
        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=monthly&window=4")
        assert resp.status_code == 200
        for bucket in resp.json()["buckets"]:
            assert bucket["period_start"].endswith("-01"), (
                f"period_start should be day 01, got: {bucket['period_start']}"
            )

    def test_monthly_bucket_order_oldest_to_newest(self, admin_client):
        """monthly 버킷 순서: 오래된 것 → 최신."""
        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=monthly&window=4")
        assert resp.status_code == 200
        starts = [b["period_start"] for b in resp.json()["buckets"]]
        assert starts == sorted(starts)

    def test_monthly_event_in_current_month_counted(self, admin_client, db):
        """이번 달 이벤트 → 현재 월 버킷(마지막)에 집계."""
        this_month = _this_month_first_naive() + timedelta(hours=10)
        db.add(UiSourceEvent(username="MONTHLY_USER", source="console", recorded_at=this_month))
        db.commit()

        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=monthly&window=3")
        assert resp.status_code == 200
        current_bucket = resp.json()["buckets"][-1]
        assert current_bucket["console_users"] == 1
        assert current_bucket["total_events"] == 1


# ── Tests: 인가 ──────────────────────────────────────────────────────────────

class TestUiSourceStatsAuthorization:

    def test_non_admin_returns_403(self, user_client):
        """일반 사용자 → 403 Forbidden."""
        resp = user_client.get("/api/v1/sessions/ui-source/stats")
        assert resp.status_code == 403

    def test_admin_can_access(self, admin_client):
        """admin 역할 → 200 OK."""
        resp = admin_client.get("/api/v1/sessions/ui-source/stats")
        assert resp.status_code == 200


# ── Tests: 파라미터 검증 ──────────────────────────────────────────────────────

class TestUiSourceStatsValidation:

    def test_window_above_52_returns_422(self, admin_client):
        """window=53 → 422 Unprocessable Entity."""
        resp = admin_client.get("/api/v1/sessions/ui-source/stats?window=53")
        assert resp.status_code == 422

    def test_window_zero_returns_422(self, admin_client):
        """window=0 → 422 Unprocessable Entity."""
        resp = admin_client.get("/api/v1/sessions/ui-source/stats?window=0")
        assert resp.status_code == 422

    def test_invalid_period_returns_422(self, admin_client):
        """period=daily → 422 Unprocessable Entity."""
        resp = admin_client.get("/api/v1/sessions/ui-source/stats?period=daily")
        assert resp.status_code == 422
