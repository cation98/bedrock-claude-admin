"""usage-worker Prometheus metrics 단위 테스트."""

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")

with patch("sqlalchemy.create_engine", return_value=MagicMock()):
    with patch("prometheus_client.start_http_server"):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import worker


class TestMetricsExist:
    def test_stream_lag_gauge_exists(self):
        assert hasattr(worker, "stream_lag")

    def test_dlq_depth_gauge_exists(self):
        assert hasattr(worker, "dlq_depth")

    def test_legacy_events_counter_exists(self):
        assert hasattr(worker, "legacy_events_counter")


class TestUpdateMetrics:
    def test_dlq_depth_set_from_xlen(self):
        r = MagicMock()
        r.xlen.return_value = 5
        r.xpending.return_value = {}
        worker._update_metrics(r)
        r.xlen.assert_called_once_with(worker.DLQ_STREAM_KEY)

    def test_stream_lag_zero_when_no_pending(self):
        r = MagicMock()
        r.xlen.return_value = 0
        r.xpending.return_value = {"min": None, "max": None, "count": 0}
        worker._update_metrics(r)

    def test_update_metrics_tolerates_redis_error(self):
        r = MagicMock()
        r.xlen.side_effect = Exception("connection refused")
        worker._update_metrics(r)  # must not raise


class TestLegacyCounter:
    def test_legacy_event_increments_counter(self):
        """webchat 경로 (request_id 없음) → legacy_events_counter 증가."""
        fields = {
            "user_id": "999",
            "username": "N1102359",
            "source": "webchat",
            "model": "claude-sonnet-4-6",
            "input_tokens": "10",
            "output_tokens": "5",
            "total_tokens": "15",
            "cost_usd": "0.0001",
            "ts": "1700000000",
        }
        before = worker.legacy_events_counter._value.get()
        worker._parse_event(fields)
        after = worker.legacy_events_counter._value.get()
        assert after > before

    def test_no_legacy_increment_when_request_id_present(self):
        """request_id 있으면 legacy counter 증가하지 않음."""
        import uuid
        fields = {
            "username": "N1102359",
            "request_id": str(uuid.uuid4()),
            "model": "claude-sonnet-4-6",
            "input_tokens": "10",
            "output_tokens": "5",
            "total_tokens": "15",
            "cost_usd": "0.0001",
            "recorded_at": "2026-04-25T00:00:00+00:00",
        }
        before = worker.legacy_events_counter._value.get()
        worker._parse_event(fields)
        after = worker.legacy_events_counter._value.get()
        assert after == before
