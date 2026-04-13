"""사용자 chat 로그 N일 이내 JSONL export.

Open WebUI `chat` 테이블 스키마 기준:
    chat(id TEXT PRIMARY KEY, user_id TEXT, created_at TIMESTAMP, title TEXT, chat JSONB)

chat JSONB 의 `messages` 배열 길이를 message_count로 export.

Usage:
    DATABASE_URL=postgresql://... \\
      python -m ops.export.chats --user <uid> --since 90 --output chats.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from ops.export._common import db_session


def _fetch_chats(*, user_id: str, since_days: int, session: Session):
    """Open WebUI chat 테이블에서 user_id + cutoff 이후 행 조회.

    Returns: SQLAlchemy Row list (id, user_id, created_at, title, message_count)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    rows = session.execute(
        text(
            """
            SELECT
                id,
                user_id,
                created_at,
                title,
                jsonb_array_length(chat->'messages') AS message_count
            FROM chat
            WHERE user_id = :uid AND created_at >= :cutoff
            ORDER BY created_at DESC
            """
        ),
        {"uid": user_id, "cutoff": cutoff},
    ).fetchall()
    return rows


def export_chats_to_jsonl(*, user_id: str, since_days: int, output_path: str) -> int:
    """JSONL 파일로 export. Return: 기록된 행 수."""
    count = 0
    with db_session() as session:
        rows = _fetch_chats(user_id=user_id, since_days=since_days, session=session)
        with open(output_path, "w", encoding="utf-8") as fp:
            for r in rows:
                record = {
                    "id": r.id,
                    "user_id": r.user_id,
                    "created_at": (
                        r.created_at.isoformat()
                        if hasattr(r.created_at, "isoformat")
                        else str(r.created_at)
                    ),
                    "title": r.title,
                    "message_count": r.message_count,
                }
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--user", required=True, help="user_id (UUID or 사번)")
    parser.add_argument("--since", type=int, default=90, help="days back (default 90)")
    parser.add_argument("--output", default="chats.jsonl", help="output JSONL path")
    args = parser.parse_args()

    count = export_chats_to_jsonl(
        user_id=args.user,
        since_days=args.since,
        output_path=args.output,
    )
    print(f"exported {count} rows → {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
