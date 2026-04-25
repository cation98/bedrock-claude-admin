"""bedrock_proxy._invoke_bedrock + _estimate_cost_usd가 4분기 가격 사용 검증."""
import inspect
from app.routers import bedrock_proxy
from app.core import pricing


def test_estimate_cost_usd_supports_four_quartiles():
    sig = inspect.signature(bedrock_proxy._estimate_cost_usd)
    assert "cache_creation" in sig.parameters or "cache_creation_input_tokens" in sig.parameters
    assert "cache_read" in sig.parameters or "cache_read_input_tokens" in sig.parameters


def test_estimate_cost_usd_uses_pricing_module():
    src = inspect.getsource(bedrock_proxy._estimate_cost_usd)
    assert "get_price_table" in src or "pricing" in src, \
        "pricing.py를 사용해야 함 — 하드코딩된 가격 금지"


def test_estimate_cost_usd_calculates_cache_creation_premium():
    """cache_creation은 input의 1.25배 가격이 곱해져야 함."""
    cost = bedrock_proxy._estimate_cost_usd(
        "global.anthropic.claude-sonnet-4-6",
        input_tokens=0, output_tokens=0,
        cache_creation=1_000_000, cache_read=0,
    )
    assert abs(cost - 3.75) < 0.001


def test_estimate_cost_usd_calculates_cache_read_discount():
    """cache_read는 input의 0.10배 가격."""
    cost = bedrock_proxy._estimate_cost_usd(
        "global.anthropic.claude-sonnet-4-6",
        input_tokens=0, output_tokens=0,
        cache_creation=0, cache_read=1_000_000,
    )
    assert abs(cost - 0.30) < 0.001
