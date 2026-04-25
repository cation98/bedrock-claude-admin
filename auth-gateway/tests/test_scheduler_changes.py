"""scheduler.py 변경 검증 — event_retention_loop 신규 + snapshot 조건부."""
import inspect
from app.core import scheduler


def test_event_retention_loop_exists():
    assert hasattr(scheduler, "event_retention_loop"), \
        "event_retention_loop 함수 신설 필요"


def test_event_retention_loop_uses_90_days():
    src = inspect.getsource(scheduler.event_retention_loop)
    assert "90 days" in src, "retention 기간 90일이어야 함"


def test_event_retention_loop_targets_token_usage_event():
    src = inspect.getsource(scheduler.event_retention_loop)
    assert "token_usage_event" in src


def test_token_snapshot_loop_respects_enabled_flag():
    src = inspect.getsource(scheduler.token_snapshot_loop)
    assert "snapshot_loop_enabled" in src, \
        "snapshot loop는 settings.snapshot_loop_enabled로 분기해야 함"
