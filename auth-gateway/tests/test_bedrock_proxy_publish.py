"""bedrock_proxy._publish_usage_event 시그니처 + 동작 검증."""
import inspect
from unittest.mock import patch, MagicMock
import pytest

from app.routers import bedrock_proxy


def test_publish_signature_has_request_id_and_source():
    sig = inspect.signature(bedrock_proxy._publish_usage_event)
    params = sig.parameters
    assert "request_id" in params, "publish는 request_id를 받아야 함 (idempotency)"
    assert "source" in params, "publish는 source를 받아야 함 ('console-cli' 등)"


def test_publish_signature_has_cache_token_params():
    sig = inspect.signature(bedrock_proxy._publish_usage_event)
    params = sig.parameters
    assert "cache_creation_input_tokens" in params
    assert "cache_read_input_tokens" in params


@patch("app.routers.bedrock_proxy.get_redis")
def test_publish_includes_cache_and_request_id_in_xadd(mock_redis_factory):
    """xadd 호출 시 fields에 request_id + cache 토큰 포함."""
    mock_r = MagicMock()
    mock_redis_factory.return_value = mock_r

    bedrock_proxy._publish_usage_event(
        request_id="test-req-id-123",
        source="console-cli",
        username="N1102359",
        model="global.anthropic.claude-sonnet-4-6",
        input_tokens=100, output_tokens=200,
        cache_creation_input_tokens=50,
        cache_read_input_tokens=25,
        cost_usd=0.005,
        cost_krw=7,
    )

    assert mock_r.xadd.called
    args, kwargs = mock_r.xadd.call_args
    fields_dict = args[1] if len(args) > 1 else args[0] if isinstance(args[0], dict) else None
    assert fields_dict is not None
    assert fields_dict["request_id"] == "test-req-id-123"
    assert fields_dict["source"] == "console-cli"
    assert fields_dict["cache_creation_input_tokens"] == "50"
    assert fields_dict["cache_read_input_tokens"] == "25"


@patch("app.routers.bedrock_proxy.get_redis")
def test_publish_uses_maxlen_to_cap_stream(mock_redis_factory):
    mock_r = MagicMock()
    mock_redis_factory.return_value = mock_r
    bedrock_proxy._publish_usage_event(
        request_id="x", source="console-cli", username="U", model="m",
        input_tokens=1, output_tokens=1, cost_usd=0.0, cost_krw=0,
    )
    _, kwargs = mock_r.xadd.call_args
    assert kwargs.get("maxlen") == 100_000
    assert kwargs.get("approximate") is True


@patch("app.routers.bedrock_proxy.get_redis", return_value=None)
def test_publish_increments_drop_counter_when_redis_unavailable(mock_redis_factory):
    """Redis 없을 때 silent fail이 아닌 drop counter 증가 + ERROR 로그."""
    assert hasattr(bedrock_proxy, "publish_drop_counter"), \
        "publish_drop_counter (Prometheus Counter)가 모듈에 정의되어야 함"
    before = _get_drop_count(bedrock_proxy.publish_drop_counter, reason="redis_unavailable")
    bedrock_proxy._publish_usage_event(
        request_id="x", source="console-cli", username="U", model="m",
        input_tokens=1, output_tokens=1, cost_usd=0.0, cost_krw=0,
    )
    after = _get_drop_count(bedrock_proxy.publish_drop_counter, reason="redis_unavailable")
    assert after == before + 1


def _get_drop_count(counter, reason: str) -> float:
    """Prometheus Counter에서 reason 라벨의 현재 값 추출."""
    for sample in counter.collect()[0].samples:
        if sample.name.endswith("_total") and sample.labels.get("reason") == reason:
            return sample.value
    return 0.0
