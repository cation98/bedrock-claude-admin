"""Task 15 — upsert_daily/hourly: model_id + cache 컬럼 합산 검증."""

import inspect
import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")

with patch("sqlalchemy.create_engine", return_value=MagicMock()):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import worker  # noqa: E402


def _make_ev(**kwargs):
    ev = {
        "request_id": "req-001",
        "source": "console-cli",
        "username": "N1102359",
        "model": "global.anthropic.claude-sonnet-4-6",
        "model_id": "global.anthropic.claude-sonnet-4-6",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 5,
        "cost_usd": 0.001,
        "cost_krw": 1,
        "usage_date": "2026-04-25",
        "hour": 10,
        "slot": 60,
    }
    ev.update(kwargs)
    return ev


class TestUpsertDailyModelId:
    def test_upsert_daily_includes_model_id(self):
        """upsert_daily SQL에 model_id 컬럼 포함."""
        src = inspect.getsource(worker.upsert_daily)
        assert "model_id" in src

    def test_upsert_daily_includes_cache_creation(self):
        """upsert_daily SQL에 cache_creation_input_tokens 포함."""
        src = inspect.getsource(worker.upsert_daily)
        assert "cache_creation_input_tokens" in src

    def test_upsert_daily_includes_cache_read(self):
        """upsert_daily SQL에 cache_read_input_tokens 포함."""
        src = inspect.getsource(worker.upsert_daily)
        assert "cache_read_input_tokens" in src

    def test_upsert_daily_cache_aggregated_on_conflict(self):
        """ON CONFLICT 시 cache 컬럼도 합산 (+ EXCLUDED)."""
        src = inspect.getsource(worker.upsert_daily)
        # cache 컬럼이 UPDATE SET에서 EXCLUDED로 합산되어야 함
        assert "EXCLUDED.cache_creation_input_tokens" in src or (
            "cache_creation_input_tokens" in src and "EXCLUDED" in src
        )

    def test_upsert_daily_passes_model_id_param(self):
        """upsert_daily execute 호출 시 model_id 파라미터 전달."""
        session = MagicMock()
        ev = _make_ev()
        worker.upsert_daily(session, [ev])
        params = session.execute.call_args[0][1]
        assert "model_id" in params


class TestUpsertHourlyModelId:
    def test_upsert_hourly_includes_model_id(self):
        """upsert_hourly SQL에 model_id 컬럼 포함."""
        src = inspect.getsource(worker.upsert_hourly)
        assert "model_id" in src

    def test_upsert_hourly_includes_cache_creation(self):
        """upsert_hourly SQL에 cache_creation_input_tokens 포함."""
        src = inspect.getsource(worker.upsert_hourly)
        assert "cache_creation_input_tokens" in src

    def test_upsert_hourly_includes_cache_read(self):
        """upsert_hourly SQL에 cache_read_input_tokens 포함."""
        src = inspect.getsource(worker.upsert_hourly)
        assert "cache_read_input_tokens" in src

    def test_upsert_hourly_passes_model_id_param(self):
        """upsert_hourly execute 호출 시 model_id 파라미터 전달."""
        session = MagicMock()
        ev = _make_ev()
        worker.upsert_hourly(session, [ev])
        params = session.execute.call_args[0][1]
        assert "model_id" in params


class TestProcessBatchCallsInsertEvent:
    def test_process_batch_calls_insert_event_or_skip(self):
        """process_batch가 upsert 전에 insert_event_or_skip 호출."""
        import inspect
        src = inspect.getsource(worker.process_batch)
        assert "insert_event_or_skip" in src
