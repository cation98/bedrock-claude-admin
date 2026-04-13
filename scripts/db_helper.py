"""CP-19 DB helper — psql 미설치 환경(CI/컨테이너) fallback.

psql CLI 의존성을 제거하고 psycopg2만 있으면 동일 동작.

사용:
  python3 scripts/db_helper.py get-user-id TESTUSER01
  python3 scripts/db_helper.py upsert-session <user_id> <username> <pod_name> <token_hash>
  python3 scripts/db_helper.py refresh-pod-token <pod_name> <fresh_hash>

DATABASE_URL 환경 변수 필수.
"""
from __future__ import annotations

import os
import sys

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 미설치. pip install psycopg2-binary", file=sys.stderr)
    sys.exit(2)


def _conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL 미설정", file=sys.stderr)
        sys.exit(2)
    return psycopg2.connect(url)


def get_user_id(username: str) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE username = %s AND is_approved = true LIMIT 1",
            (username,),
        )
        row = cur.fetchone()
        if row:
            print(row[0])
        else:
            sys.exit(1)


def upsert_session(user_id: str, username: str, pod_name: str, token_hash: str) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO terminal_sessions
                (user_id, username, pod_name, pod_status, pod_token_hash,
                 session_type, started_at, created_at, last_active_at)
            VALUES (%s, %s, %s, 'running', %s, 'workshop', NOW(), NOW(), NOW())
            ON CONFLICT (pod_name) DO UPDATE SET
                user_id        = EXCLUDED.user_id,
                username       = EXCLUDED.username,
                pod_status     = 'running',
                pod_token_hash = EXCLUDED.pod_token_hash,
                last_active_at = NOW()
            """,
            (int(user_id), username, pod_name, token_hash),
        )
        c.commit()


def refresh_pod_token(pod_name: str, fresh_hash: str) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE terminal_sessions SET pod_token_hash = %s, last_active_at = NOW() WHERE pod_name = %s",
            (fresh_hash, pod_name),
        )
        c.commit()


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == "get-user-id" and len(args) == 1:
        get_user_id(args[0])
    elif cmd == "upsert-session" and len(args) == 4:
        upsert_session(*args)
    elif cmd == "refresh-pod-token" and len(args) == 2:
        refresh_pod_token(*args)
    else:
        print(f"Unknown command or bad args: {cmd} {args}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
