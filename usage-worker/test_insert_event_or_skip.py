"""Task 14 — insert_event_or_skip: TokenUsageEvent 멱등성 dedupe 검증."""

import os
import sys
import uuid
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")

with patch("sqlalchemy.create_engine", return_value=MagicMock()):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import worker  # noqa: E402


def _make_ev(**kwargs):
    ev = {
        "request_id": str(uuid.uuid4()),
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


class TestInsertEventOrSkip:
    def test_function_exists(self):
        """insert_event_or_skip 함수 존재."""
        assert hasattr(worker, "insert_event_or_skip"), (
            "worker.py에 insert_event_or_skip 함수가 없음"
        )

    def test_calls_execute_with_request_id(self):
        """request_id를 ON CONFLICT DO NOTHING INSERT에 전달."""
        session = MagicMock()
        ev = _make_ev()
        worker.insert_event_or_skip(session, ev)
        assert session.execute.called
        # SQL에 request_id 파라미터 포함 여부
        call_kwargs = session.execute.call_args
        # params dict에 request_id 있어야 함
        params = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1]
        if isinstance(params, dict):
            assert "request_id" in params
        else:
            # text() 쿼리 + params가 두 번째 인수로 넘어가는 경우
            all_calls_str = str(session.execute.call_args_list)
            assert "request_id" in all_calls_str

    def test_insert_contains_on_conflict_do_nothing(self):
        """INSERT SQL에 ON CONFLICT DO NOTHING 포함 (idempotency)."""
        import inspect
        src = inspect.getsource(worker.insert_event_or_skip)
        assert "ON CONFLICT" in src.upper()
        assert "DO NOTHING" in src.upper()

    def test_insert_targets_token_usage_event_table(self):
        """token_usage_event 테이블에 INSERT."""
        import inspect
        src = inspect.getsource(worker.insert_event_or_skip)
        assert "token_usage_event" in src

    def test_source_field_inserted(self):
        """source 필드가 INSERT 파라미터에 포함."""
        import inspect
        src = inspect.getsource(worker.insert_event_or_skip)
        assert "source" in src

    def test_cache_tokens_inserted(self):
        """cache_creation_input_tokens, cache_read_input_tokens INSERT."""
        import inspect
        src = inspect.getsource(worker.insert_event_or_skip)
        assert "cache_creation_input_tokens" in src
        assert "cache_read_input_tokens" in src

    def test_model_id_inserted(self):
        """model_id INSERT."""
        import inspect
        src = inspect.getsource(worker.insert_event_or_skip)
        assert "model_id" in src
