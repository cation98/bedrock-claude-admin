"""DomainWhitelist 서비스 단위 테스트.

화이트리스트 매칭 로직을 검증:
- 정확한 도메인 매칭
- 와일드카드 도메인 매칭 (dot-prefix 보안 포함)
- 비활성 도메인 차단
- 캐시 갱신
- 빈 화이트리스트
- 대소문자 무시
"""

import time

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.proxy import AllowedDomain
from app.services.domain_whitelist import DomainWhitelist, CACHE_TTL_SECONDS


# --------------- Test DB Setup ---------------

_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_test_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_TestSession = sessionmaker(bind=_test_engine)


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
def whitelist():
    return DomainWhitelist()


# --------------- Tests ---------------


def test_exact_match_allowed(db, whitelist):
    """정확한 도메인 매칭 — 등록된 도메인은 허용."""
    db.add(AllowedDomain(domain="apis.data.go.kr", is_wildcard=False, enabled=True))
    db.commit()

    assert whitelist.is_allowed("apis.data.go.kr", db) is True


def test_exact_match_denied(db, whitelist):
    """정확한 도메인 매칭 — 미등록 도메인은 차단."""
    db.add(AllowedDomain(domain="apis.data.go.kr", is_wildcard=False, enabled=True))
    db.commit()

    assert whitelist.is_allowed("evil.example.com", db) is False


def test_wildcard_match(db, whitelist):
    """와일드카드 매칭 — *.amazonaws.com → bedrock.us-east-1.amazonaws.com 허용."""
    db.add(AllowedDomain(domain="*.amazonaws.com", is_wildcard=True, enabled=True))
    db.commit()

    assert whitelist.is_allowed("bedrock.us-east-1.amazonaws.com", db) is True
    # 베이스 도메인 자체도 허용
    assert whitelist.is_allowed("amazonaws.com", db) is True


def test_wildcard_no_match_evil_domain(db, whitelist):
    """와일드카드 보안 — dot-prefix 필수로 evilamazonaws.com 차단."""
    db.add(AllowedDomain(domain="*.amazonaws.com", is_wildcard=True, enabled=True))
    db.commit()

    # 'evilamazonaws.com'은 '.amazonaws.com'으로 끝나지 않으므로 차단
    assert whitelist.is_allowed("evilamazonaws.com", db) is False


def test_disabled_domain_denied(db, whitelist):
    """비활성 도메인은 차단."""
    db.add(AllowedDomain(domain="apis.data.go.kr", is_wildcard=False, enabled=False))
    db.commit()

    assert whitelist.is_allowed("apis.data.go.kr", db) is False


def test_cache_refresh_on_ttl_expiry(db, whitelist):
    """캐시 TTL 만료 시 DB에서 다시 로드."""
    db.add(AllowedDomain(domain="apis.data.go.kr", is_wildcard=False, enabled=True))
    db.commit()

    # 첫 번째 조회 — 캐시 로드
    assert whitelist.is_allowed("apis.data.go.kr", db) is True

    # 새 도메인 추가 (캐시에는 아직 없음)
    db.add(AllowedDomain(domain="new.example.com", is_wildcard=False, enabled=True))
    db.commit()

    # 캐시가 아직 유효하면 새 도메인 못 찾음
    assert whitelist.is_allowed("new.example.com", db) is False

    # 캐시 만료 강제 시뮬레이션
    whitelist._last_refresh = time.monotonic() - CACHE_TTL_SECONDS - 1

    # 이제 새 도메인도 허용됨
    assert whitelist.is_allowed("new.example.com", db) is True


def test_empty_whitelist_denies_all(db, whitelist):
    """빈 화이트리스트는 모든 도메인을 차단."""
    assert whitelist.is_allowed("any.domain.com", db) is False
    assert whitelist.is_allowed("google.com", db) is False


def test_case_insensitive_matching(db, whitelist):
    """대소문자 무시 매칭."""
    db.add(AllowedDomain(domain="Apis.Data.GO.kr", is_wildcard=False, enabled=True))
    db.add(AllowedDomain(domain="*.AMAZONAWS.com", is_wildcard=True, enabled=True))
    db.commit()

    assert whitelist.is_allowed("apis.data.go.kr", db) is True
    assert whitelist.is_allowed("APIS.DATA.GO.KR", db) is True
    assert whitelist.is_allowed("bedrock.us-east-1.amazonaws.com", db) is True
    assert whitelist.is_allowed("BEDROCK.US-EAST-1.AMAZONAWS.COM", db) is True
