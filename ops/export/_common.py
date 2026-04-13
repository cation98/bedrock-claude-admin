"""Export 스크립트 공통: DB 세션 + PII 마스킹.

Phase 1a 데이터 권리(ISMS-P) 대응 도구 모음의 공통 의존성.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

_EMAIL_RE = re.compile(r"^([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@(.+)$")
_PHONE_KR_RE = re.compile(r"^(\d{3})-\d{3,4}-\d{4}$")


def mask_pii(value: Optional[str]) -> Optional[str]:
    """이메일 local part + 한국 전화번호 마스킹.

    Examples:
        "alice@skons.net" -> "a***@skons.net"
        "010-1234-5678"   -> "010-****-****"
        None              -> None
        "plain text"      -> "plain text" (unchanged)
    """
    if value is None:
        return None

    m = _EMAIL_RE.match(value)
    if m:
        first = m.group(1)
        domain = m.group(2)
        return f"{first}***@{domain}"

    m = _PHONE_KR_RE.match(value)
    if m:
        prefix = m.group(1)
        return f"{prefix}-****-****"

    return value


def resolve_username(session: Session, user_id: str) -> Optional[str]:
    """user_id (UUID or 사번) → SK 사번(username) 조회.

    Fallback: 미발견 시 user_id 그대로 반환 (import 경로 단순화).
    """
    row = session.execute(
        text("SELECT username FROM users WHERE id = :uid OR username = :uid LIMIT 1"),
        {"uid": user_id},
    ).fetchone()
    return row[0] if row else user_id


@contextmanager
def db_session() -> Iterator[Session]:
    """DATABASE_URL 환경변수 기반 SQLAlchemy Session 컨텍스트.

    Yields:
        SQLAlchemy Session (사용 후 자동 close).

    Raises:
        RuntimeError: DATABASE_URL 미설정 시.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL env var required for ops/export scripts")

    engine = create_engine(url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
