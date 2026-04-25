"""TokenUsageDaily/Hourly 컬럼 확장 + TokenUsageEvent 신규 검증."""
from app.models.token_usage import (
    TokenUsageDaily,
    TokenUsageHourly,
    TokenUsageEvent,
)


def test_daily_has_model_id_column():
    cols = {c.name for c in TokenUsageDaily.__table__.columns}
    assert "model_id" in cols


def test_daily_has_cache_creation_column():
    cols = {c.name for c in TokenUsageDaily.__table__.columns}
    assert "cache_creation_input_tokens" in cols


def test_daily_has_cache_read_column():
    cols = {c.name for c in TokenUsageDaily.__table__.columns}
    assert "cache_read_input_tokens" in cols


def test_daily_unique_constraint_includes_model_id():
    constraints = [c for c in TokenUsageDaily.__table__.constraints
                   if c.__class__.__name__ == "UniqueConstraint"]
    assert any(
        {col.name for col in c.columns} == {"username", "usage_date", "model_id"}
        for c in constraints
    ), "uq must be (username, usage_date, model_id)"


def test_daily_has_index_on_usage_date_model():
    indexes = [i.name for i in TokenUsageDaily.__table__.indexes]
    assert "ix_usage_date_model" in indexes


def test_daily_has_index_on_username_usage_date():
    indexes = [i.name for i in TokenUsageDaily.__table__.indexes]
    assert "ix_username_usage_date" in indexes


def test_hourly_has_model_id_in_unique_constraint():
    constraints = [c for c in TokenUsageHourly.__table__.constraints
                   if c.__class__.__name__ == "UniqueConstraint"]
    assert any(
        {"model_id"}.issubset({col.name for col in c.columns})
        for c in constraints
    )


def test_hourly_has_cache_columns():
    cols = {c.name for c in TokenUsageHourly.__table__.columns}
    assert "cache_creation_input_tokens" in cols
    assert "cache_read_input_tokens" in cols


def test_event_table_exists_with_request_id_pk():
    cols = {c.name: c for c in TokenUsageEvent.__table__.columns}
    assert "request_id" in cols
    assert cols["request_id"].primary_key is True


def test_event_has_required_columns():
    cols = {c.name for c in TokenUsageEvent.__table__.columns}
    required = {
        "request_id", "username", "model_id", "recorded_at", "source",
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
        "cost_usd", "created_at",
    }
    assert required.issubset(cols)


def test_event_has_recorded_at_index():
    indexes = [i.name for i in TokenUsageEvent.__table__.indexes]
    assert "ix_event_recorded_at" in indexes
