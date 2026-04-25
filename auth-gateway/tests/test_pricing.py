"""pricing.py — 가격표 + 환율 단일 출처 테스트."""
import pytest

from app.core.pricing import (
    KRW_RATE,
    PRICE_TABLE,
    get_price_table,
)


def test_krw_rate_is_int_1400():
    assert KRW_RATE == 1400


def test_price_table_has_three_models():
    assert set(PRICE_TABLE.keys()) == {
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-opus-4-6",
    }


def test_price_table_has_four_quartiles_per_model():
    expected_keys = {"input", "output", "cache_creation", "cache_read"}
    for model, prices in PRICE_TABLE.items():
        assert set(prices.keys()) == expected_keys, f"{model} missing keys"


def test_cache_creation_is_125pct_of_input():
    for model, p in PRICE_TABLE.items():
        assert p["cache_creation"] == pytest.approx(p["input"] * 1.25), (
            f"{model} cache_creation should be 1.25x input"
        )


def test_cache_read_is_10pct_of_input():
    for model, p in PRICE_TABLE.items():
        assert p["cache_read"] == pytest.approx(p["input"] * 0.10), (
            f"{model} cache_read should be 0.10x input"
        )


def test_get_price_table_resolves_global_sonnet():
    p = get_price_table("global.anthropic.claude-sonnet-4-6")
    assert p["input"] == 3.0
    assert p["output"] == 15.0


def test_get_price_table_resolves_us_haiku():
    p = get_price_table("us.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert p["input"] == 0.80
    assert p["output"] == 4.0


def test_get_price_table_resolves_global_opus():
    p = get_price_table("global.anthropic.claude-opus-4-6")
    assert p["input"] == 15.0
    assert p["output"] == 75.0


def test_get_price_table_unknown_defaults_to_sonnet():
    p = get_price_table("anthropic.claude-future-model-2099")
    assert p["input"] == 3.0
