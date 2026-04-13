"""Phase 1a: ops/export/usage.py — token_usage_daily Parquet export."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pyarrow.parquet as pq

from ops.export.usage import export_usage_to_parquet


def test_export_usage_schema(tmp_path):
    rows = [
        SimpleNamespace(
            date="2026-04-01", username="u1",
            input_tokens=1000, output_tokens=500, total_cost_usd=0.03,
        ),
        SimpleNamespace(
            date="2026-04-02", username="u1",
            input_tokens=2000, output_tokens=1000, total_cost_usd=0.06,
        ),
    ]
    with patch("ops.export.usage._fetch_usage", return_value=rows), \
         patch("ops.export.usage.db_session") as mock_session:
        mock_session.return_value.__enter__.return_value = MagicMock()
        output = tmp_path / "usage.parquet"
        count = export_usage_to_parquet(since_date="2026-04-01", output_path=str(output))

    assert count == 2
    table = pq.read_table(str(output))
    assert table.num_rows == 2
    assert set(table.column_names) == {
        "date", "username", "input_tokens", "output_tokens", "total_cost_usd",
    }


def test_export_usage_empty(tmp_path):
    with patch("ops.export.usage._fetch_usage", return_value=[]), \
         patch("ops.export.usage.db_session") as mock_session:
        mock_session.return_value.__enter__.return_value = MagicMock()
        output = tmp_path / "empty.parquet"
        count = export_usage_to_parquet(since_date="2026-04-01", output_path=str(output))

    assert count == 0
    table = pq.read_table(str(output))
    assert table.num_rows == 0
    # empty도 스키마 존재
    assert set(table.column_names) == {
        "date", "username", "input_tokens", "output_tokens", "total_cost_usd",
    }
