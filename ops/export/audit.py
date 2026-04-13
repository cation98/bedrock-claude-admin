"""FileAuditLog JSONL export + PII masking.

ISMS-P 제35조 대응: 파일 접근/변경 감사 로그를 JSONL 형식으로 export.
`user_email`은 자동 마스킹(`mask_pii`).

Usage:
    DATABASE_URL=postgresql://... \\
      python -m ops.export.audit --since-days 30 --output audit.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from ops.export._common import db_session, mask_pii


def _fetch_audit(*, since_days: int, session: Session):
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    rows = session.execute(
        text(
            """
            SELECT id, user_email, file_path, action, created_at
            FROM file_audit_logs
            WHERE created_at >= :cutoff
            ORDER BY created_at DESC
            """
        ),
        {"cutoff": cutoff},
    ).fetchall()
    return rows


def export_audit_to_jsonl(*, since_days: int, output_path: str) -> int:
    count = 0
    with db_session() as session:
        rows = _fetch_audit(since_days=since_days, session=session)
        with open(output_path, "w", encoding="utf-8") as fp:
            for r in rows:
                record = {
                    "id": r.id,
                    "user_email": mask_pii(r.user_email),
                    "file_path": r.file_path,
                    "action": r.action,
                    "created_at": (
                        r.created_at.isoformat()
                        if hasattr(r.created_at, "isoformat")
                        else str(r.created_at)
                    ),
                }
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-days", type=int, default=30,
                        help="days back (default 30)")
    parser.add_argument("--output", default="audit.jsonl")
    args = parser.parse_args()

    count = export_audit_to_jsonl(
        since_days=args.since_days, output_path=args.output,
    )
    print(f"exported {count} rows → {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
