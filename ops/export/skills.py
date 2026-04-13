"""Skills 목록 CSV export (approval_status 필터).

PIPA 직무분리(SoD) 용도: 승인된 스킬 vs 대기/반려 목록.

Usage:
    DATABASE_URL=postgresql://... \\
      python -m ops.export.skills --status approved --output skills.csv
"""
from __future__ import annotations

import argparse
import csv
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

from ops.export._common import db_session


def _fetch_skills(*, approval_status: str, session: Session):
    rows = session.execute(
        text(
            """
            SELECT id, name, approval_status, author
            FROM skills
            WHERE approval_status = :st
            ORDER BY name
            """
        ),
        {"st": approval_status},
    ).fetchall()
    return rows


def export_skills_to_csv(*, approval_status: str, output_path: str) -> int:
    """CSV 파일로 export. 헤더 항상 작성. Return: 데이터 행 수."""
    count = 0
    with db_session() as session:
        rows = _fetch_skills(approval_status=approval_status, session=session)
        with open(output_path, "w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(["id", "name", "approval_status", "author"])
            for r in rows:
                writer.writerow([r.id, r.name, r.approval_status, r.author])
                count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--status",
        default="approved",
        choices=["approved", "pending", "rejected"],
        help="approval_status filter",
    )
    parser.add_argument("--output", default="skills.csv")
    args = parser.parse_args()

    count = export_skills_to_csv(
        approval_status=args.status, output_path=args.output,
    )
    print(f"exported {count} rows → {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
