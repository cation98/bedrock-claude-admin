"""Task 16 — DLQ: 영구 실패 이벤트 격리 검증."""

import inspect
import os
import sys
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")

with patch("sqlalchemy.create_engine", return_value=MagicMock()):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import worker  # noqa: E402


class TestDLQConstants:
    def test_max_retries_constant_exists(self):
        """MAX_RETRIES 상수 정의."""
        assert hasattr(worker, "MAX_RETRIES")

    def test_max_retries_is_3(self):
        """MAX_RETRIES = 3 (DLQ 이전 최대 재시도)."""
        assert worker.MAX_RETRIES == 3

    def test_dlq_stream_key_exists(self):
        """DLQ_STREAM_KEY 상수 정의."""
        assert hasattr(worker, "DLQ_STREAM_KEY")

    def test_dlq_stream_key_value(self):
        """DLQ 스트림 이름: stream:usage_events_dlq."""
        assert worker.DLQ_STREAM_KEY == "stream:usage_events_dlq"


class TestPublishToDlq:
    def test_publish_to_dlq_function_exists(self):
        """publish_to_dlq 함수 존재."""
        assert hasattr(worker, "publish_to_dlq")

    def test_publish_to_dlq_calls_xadd(self):
        """publish_to_dlq가 DLQ 스트림에 XADD."""
        r = MagicMock()
        worker.publish_to_dlq(r, "bad-msg-id", {"username": "N1102359"}, "DB error")
        r.xadd.assert_called_once()
        call_args = r.xadd.call_args
        # 첫 번째 인수가 DLQ_STREAM_KEY
        assert call_args[0][0] == worker.DLQ_STREAM_KEY

    def test_publish_to_dlq_includes_original_msg_id(self):
        """DLQ 페이로드에 원본 message_id 포함."""
        r = MagicMock()
        worker.publish_to_dlq(r, "msg-123", {"username": "N1102359"}, "timeout")
        call_args = r.xadd.call_args
        payload = call_args[0][1]
        assert "original_msg_id" in payload or "msg_id" in payload or "msg-123" in str(payload)

    def test_publish_to_dlq_includes_error_reason(self):
        """DLQ 페이로드에 에러 이유 포함."""
        r = MagicMock()
        worker.publish_to_dlq(r, "msg-456", {}, "Connection refused")
        call_args = r.xadd.call_args
        payload = call_args[0][1]
        assert "Connection refused" in str(payload)

    def test_publish_to_dlq_includes_failed_at(self):
        """DLQ 페이로드에 failed_at 타임스탬프 포함."""
        r = MagicMock()
        worker.publish_to_dlq(r, "msg-789", {}, "err")
        call_args = r.xadd.call_args
        payload = call_args[0][1]
        assert "failed_at" in payload


class TestDLQIntegration:
    def test_process_batch_source_inspectable(self):
        """process_batch 소스에 MAX_RETRIES 참조 또는 DLQ 로직 포함."""
        src = inspect.getsource(worker.process_batch)
        # process_batch 자체가 DLQ를 호출하거나 retry count를 확인해야 함.
        # 또는 _consume_loop에서 retry를 관리할 수 있음.
        consume_src = inspect.getsource(worker._consume_loop)
        has_dlq_in_batch = "DLQ" in src.upper() or "dlq" in src or "MAX_RETRIES" in src
        has_dlq_in_loop = "DLQ" in consume_src.upper() or "dlq" in consume_src or "MAX_RETRIES" in consume_src
        assert has_dlq_in_batch or has_dlq_in_loop, (
            "process_batch 또는 _consume_loop에 DLQ/MAX_RETRIES 로직이 없음"
        )
