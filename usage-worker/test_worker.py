"""usage-worker unit tests.

DB/Redis 미접속 환경에서도 동작하도록 MagicMock으로 외부 의존 주입.
"""

import os
import sys
from unittest.mock import MagicMock, patch

# worker 모듈이 import 시점에 환경변수를 읽으므로 먼저 세팅
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")

# SQLAlchemy engine 생성은 import 시점에 실행되지만 실제 연결은 pool_pre_ping에서만.
# fakeredis 없이 worker 전체 import 테스트 위해 engine 생성을 MagicMock으로 대체.
with patch("sqlalchemy.create_engine", return_value=MagicMock()):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import worker  # noqa: E402


def _make_event_fields(username="N1102359", input_tokens="100", output_tokens="50"):
    return {
        "username": username,
        "model": "claude-sonnet-4-6",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": str(int(input_tokens) + int(output_tokens)),
        "cost_usd": "0.001",
        "cost_krw": "1",
        "recorded_at": "2026-04-14T00:00:00+00:00",
    }


class TestClaimStalePending:
    """XAUTOCLAIM 기반 dead consumer 복구 로직."""

    def test_no_stale_messages_returns_zero(self):
        """pending 없음 → 0 반환, process_batch 호출 없음."""
        r = MagicMock()
        # XAUTOCLAIM: 빈 리스트 + cursor "0-0" (완료 신호)
        r.xautoclaim.return_value = ("0-0", [], [])

        with patch("worker.process_batch") as pb:
            count = worker._claim_stale_pending(r)

        assert count == 0
        pb.assert_not_called()
        r.xautoclaim.assert_called_once()

    def test_claims_and_processes_stale_messages(self):
        """idle > STALE_IDLE_MS 메시지를 claim → parse → process_batch 실행."""
        r = MagicMock()
        fields = _make_event_fields()
        r.xautoclaim.side_effect = [
            ("0-0", [("1234-0", fields), ("1234-1", fields)], []),
        ]
        # delivery_count=1 (< MAX_RETRIES=3) → DLQ 경유 없이 정상 처리
        r.xpending_range.return_value = [{"times_delivered": 1}]

        with patch("worker.process_batch") as pb:
            count = worker._claim_stale_pending(r)

        assert count == 2
        pb.assert_called_once()
        # process_batch 호출 시 message_ids 2개, events 2개
        args = pb.call_args
        assert args.args[1] == ["1234-0", "1234-1"]
        assert len(args.args[2]) == 2

    def test_parse_failure_acks_without_processing(self):
        """parse 실패 메시지는 ACK하여 PEL 제거, process_batch 없음."""
        r = MagicMock()
        bad_fields = {"no_username": "x"}  # username / user_id 둘 다 없음
        r.xautoclaim.side_effect = [
            ("0-0", [("9999-0", bad_fields)], []),
        ]
        # delivery_count=1 (< MAX_RETRIES) → DLQ 경유 없이 parse 시도 → parse 실패 → ACK
        r.xpending_range.return_value = [{"times_delivered": 1}]

        with patch("worker.process_batch") as pb:
            count = worker._claim_stale_pending(r)

        assert count == 0
        pb.assert_not_called()
        r.xack.assert_called_once_with(worker.STREAM_KEY, worker.CONSUMER_GROUP, "9999-0")

    def test_cursor_pagination_continues_until_zero(self):
        """cursor != "0-0" 인 동안 반복 호출, "0-0" 도달 시 종료."""
        r = MagicMock()
        fields = _make_event_fields()
        r.xautoclaim.side_effect = [
            ("200-0", [("100-0", fields)], []),
            ("0-0", [("200-0", fields)], []),
        ]
        # 모든 메시지 delivery_count=1 → 정상 처리
        r.xpending_range.return_value = [{"times_delivered": 1}]

        with patch("worker.process_batch") as pb:
            count = worker._claim_stale_pending(r)

        assert count == 2
        assert r.xautoclaim.call_count == 2
        assert pb.call_count == 2

    def test_legacy_two_tuple_return_supported(self):
        """일부 redis-py 구현이 (cursor, messages) 2-tuple 반환 — 호환 유지."""
        r = MagicMock()
        fields = _make_event_fields()
        # 3-tuple 이 아닌 2-tuple
        r.xautoclaim.return_value = ("0-0", [("500-0", fields)])
        # delivery_count=1 → 정상 처리
        r.xpending_range.return_value = [{"times_delivered": 1}]

        with patch("worker.process_batch") as pb:
            count = worker._claim_stale_pending(r)

        assert count == 1
        pb.assert_called_once()

    def test_min_idle_time_is_passed_to_xautoclaim(self):
        """XAUTOCLAIM 호출 시 STALE_IDLE_MS 전달 — 너무 최근 메시지 보호."""
        r = MagicMock()
        r.xautoclaim.return_value = ("0-0", [], [])

        worker._claim_stale_pending(r)

        kwargs = r.xautoclaim.call_args.kwargs
        assert kwargs["min_idle_time"] == worker.STALE_IDLE_MS
        assert kwargs["start_id"] == "0-0"
        assert kwargs["count"] == worker.CLAIM_BATCH

    def test_dlq_triggered_when_delivery_count_exceeds_max_retries(self):
        """delivery_count >= MAX_RETRIES → publish_to_dlq + xack, process_batch 호출 없음."""
        r = MagicMock()
        fields = _make_event_fields()
        r.xautoclaim.return_value = ("0-0", [("777-0", fields)], [])
        # delivery_count=MAX_RETRIES → DLQ 이동
        r.xpending_range.return_value = [{"times_delivered": worker.MAX_RETRIES}]

        with patch("worker.process_batch") as pb, patch("worker.publish_to_dlq") as dlq:
            count = worker._claim_stale_pending(r)

        assert count == 0
        pb.assert_not_called()
        dlq.assert_called_once()
        r.xack.assert_called_once_with(worker.STREAM_KEY, worker.CONSUMER_GROUP, "777-0")
