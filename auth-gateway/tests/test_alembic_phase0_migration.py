"""T7 TDD — Phase 0 누락 테이블 Alembic migration smoke test.

검증 범위:
  1. env.py가 Phase 0 모델 전체를 import하는지 확인 (autogenerate 전제조건)
  2. Phase 0 migration upgrade()가 5개 신규 테이블을 생성하는지 확인
  3. Phase 0 migration downgrade()가 해당 테이블을 제거하는지 확인

신규 테이블 (초기 migration에 없었던 것):
  - sqlcipher_keys    (SQLCipherKey — app/services/sqlcipher_service.py)
  - app_likes         (AppLike — app/models/app.py)
  - announcements     (Announcement — app/models/announcement.py)
  - guides            (Guide — app/models/guide.py)
  - moderation_violations  (ModerationViolation — app/models/moderation.py)
"""

import os

os.environ.setdefault(
    "ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-32-chars-min-xx"
)

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

# ── 마이그레이션 파일 경로 (env.py 기준 상대경로가 아닌 테스트 실행 기준) ──
_MIGRATION_FILE = Path(__file__).parent.parent / "alembic" / "versions" / "b2c3d4e5f6a7_phase0_missing_tables.py"


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def sqlite_engine():
    """PostgreSQL 독립 SQLite in-memory 엔진."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


def _load_migration_module():
    """alembic/versions/b2c3d4e5f6a7_phase0_missing_tables.py 동적 로드."""
    if not _MIGRATION_FILE.exists():
        pytest.fail(
            f"Migration file not found: {_MIGRATION_FILE}\n"
            "alembic/versions/b2c3d4e5f6a7_phase0_missing_tables.py 를 생성하세요."
        )
    spec = importlib.util.spec_from_file_location("phase0_migration", _MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─── 1. env.py import 완전성 ─────────────────────────────────────────────────

class TestEnvPyModelCoverage:
    """alembic/env.py 가 Phase 0 신규 모델을 모두 import하는지 확인.

    autogenerate 시 Base.metadata에 등록되지 않은 모델은 감지되지 않는다.
    env.py에 import 문이 없으면 alembic revision --autogenerate 결과에
    해당 테이블이 누락된다.
    """

    def _env_py_text(self) -> str:
        env_path = Path(__file__).parent.parent / "alembic" / "env.py"
        return env_path.read_text()

    def test_sqlcipher_key_in_env_py(self):
        """env.py 가 SQLCipherKey (sqlcipher_keys)를 import해야 한다."""
        assert "SQLCipherKey" in self._env_py_text(), (
            "alembic/env.py에 SQLCipherKey import가 없습니다. "
            "from app.services.sqlcipher_service import SQLCipherKey 추가 필요."
        )

    def test_app_like_in_env_py(self):
        """env.py 가 AppLike (app_likes)를 import해야 한다."""
        assert "AppLike" in self._env_py_text(), (
            "alembic/env.py에 AppLike import가 없습니다. "
            "app.models.app import 라인에 AppLike 추가 필요."
        )

    def test_announcement_in_env_py(self):
        """env.py 가 Announcement (announcements)를 import해야 한다."""
        assert "Announcement" in self._env_py_text(), (
            "alembic/env.py에 Announcement import가 없습니다."
        )

    def test_guide_in_env_py(self):
        """env.py 가 Guide (guides)를 import해야 한다."""
        assert "Guide" in self._env_py_text(), (
            "alembic/env.py에 Guide import가 없습니다."
        )

    def test_moderation_violation_in_env_py(self):
        """env.py 가 ModerationViolation (moderation_violations)를 import해야 한다."""
        assert "ModerationViolation" in self._env_py_text(), (
            "alembic/env.py에 ModerationViolation import가 없습니다."
        )


# ─── 2. Migration 파일 구조 검증 ─────────────────────────────────────────────

class TestMigrationFileStructure:
    """migration 파일이 올바른 revision chain을 가지는지 확인."""

    def test_migration_file_exists(self):
        """b2c3d4e5f6a7_phase0_missing_tables.py 파일이 존재해야 한다."""
        assert _MIGRATION_FILE.exists(), (
            f"Migration file not found: {_MIGRATION_FILE}"
        )

    def test_migration_revision_id(self):
        """revision ID가 'b2c3d4e5f6a7'여야 한다."""
        mod = _load_migration_module()
        assert mod.revision == "b2c3d4e5f6a7"

    def test_migration_down_revision(self):
        """down_revision이 기존 마지막 migration 'a1f2c3d4e5f6'이어야 한다."""
        mod = _load_migration_module()
        assert mod.down_revision == "a1f2c3d4e5f6"

    def test_migration_has_upgrade(self):
        """upgrade() 함수가 정의되어 있어야 한다."""
        mod = _load_migration_module()
        assert callable(getattr(mod, "upgrade", None))

    def test_migration_has_downgrade(self):
        """downgrade() 함수가 정의되어 있어야 한다."""
        mod = _load_migration_module()
        assert callable(getattr(mod, "downgrade", None))


# ─── 3. SQLite upgrade / downgrade 동작 검증 ─────────────────────────────────

NEW_TABLES = {
    "sqlcipher_keys",
    "app_likes",
    "announcements",
    "guides",
    "moderation_violations",
}


class TestPhase0MigrationUpgradeDowngrade:
    """upgrade() / downgrade()가 SQLite에서 올바르게 동작하는지 확인.

    PostgreSQL 전용 타입(JSONB 등)을 사용하지 않는 테이블만 대상이므로
    SQLite in-memory DB로 검증 가능하다.
    """

    def _run_upgrade(self, conn):
        """MigrationContext + Operations.context()를 이용해 upgrade() 실행.

        alembic 1.14+ 기준: Operations.context(ctx) context manager로
        alembic.op 모듈의 현재 컨텍스트를 설정한다.
        """
        mod = _load_migration_module()
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()

    def _run_downgrade(self, conn):
        mod = _load_migration_module()
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.downgrade()

    def test_upgrade_creates_new_tables(self, sqlite_engine):
        """upgrade()를 실행하면 5개 신규 테이블이 생성된다."""
        with sqlite_engine.connect() as conn:
            with conn.begin():
                self._run_upgrade(conn)

        inspector = inspect(sqlite_engine)
        existing = set(inspector.get_table_names())
        missing = NEW_TABLES - existing
        assert not missing, f"upgrade 후 테이블 누락: {missing}"

    def test_downgrade_drops_new_tables(self, sqlite_engine):
        """downgrade()를 실행하면 5개 신규 테이블이 제거된다."""
        with sqlite_engine.connect() as conn:
            with conn.begin():
                self._run_upgrade(conn)

        with sqlite_engine.connect() as conn:
            with conn.begin():
                self._run_downgrade(conn)

        inspector = inspect(sqlite_engine)
        remaining = set(inspector.get_table_names()) & NEW_TABLES
        assert not remaining, f"downgrade 후 테이블이 남아있음: {remaining}"
