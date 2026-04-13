"""token_usage_daily Parquet export.

Platform RDS `token_usage_daily` 테이블 집계 데이터를 Parquet로 저장.
분석 도구(Pandas, DuckDB, Spark) 호환.

Usage:
    DATABASE_URL=postgresql://... \\
      python -m ops.export.usage --since 2026-04-01 --output usage.parquet
"""
from __future__ import annotations

import argparse
import sys

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from sqlalchemy.orm import Session

from ops.export._common import db_session


_SCHEMA = pa.schema([
    pa.field("date", pa.string()),
    pa.field("username", pa.string()),
    pa.field("input_tokens", pa.int64()),
    pa.field("output_tokens", pa.int64()),
    pa.field("total_cost_usd", pa.float64()),
])


def _fetch_usage(*, since_date: str, session: Session):
    """token_usage_daily 테이블에서 since_date 이후 행 조회.

    Returns: SQLAlchemy Row list (date, username, input_tokens, output_tokens, total_cost_usd)
    """
    rows = session.execute(
        text(
            """
            SELECT date, username,
                   input_tokens, output_tokens, total_cost_usd
            FROM token_usage_daily
            WHERE date >= :since
            ORDER BY date, username
            """
        ),
        {"since": since_date},
    ).fetchall()
    return rows


def export_usage_to_parquet(*, since_date: str, output_path: str) -> int:
    """Parquet 파일로 export. 빈 결과도 스키마 포함하여 저장. Return: 행 수."""
    with db_session() as session:
        rows = _fetch_usage(since_date=since_date, session=session)

    table = pa.table(
        {
            "date": [str(r.date) for r in rows],
            "username": [r.username for r in rows],
            "input_tokens": [int(r.input_tokens) for r in rows],
            "output_tokens": [int(r.output_tokens) for r in rows],
            "total_cost_usd": [float(r.total_cost_usd) for r in rows],
        },
        schema=_SCHEMA,
    )
    pq.write_table(table, output_path)
    return table.num_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--since", required=True, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--output", default="usage.parquet")
    args = parser.parse_args()

    count = export_usage_to_parquet(
        since_date=args.since, output_path=args.output,
    )
    print(f"exported {count} rows → {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
