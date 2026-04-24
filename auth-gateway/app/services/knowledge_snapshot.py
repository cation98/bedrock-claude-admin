"""daily/weekly/monthly 지식 스냅샷 집계 서비스."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeMention, KnowledgeSnapshot

logger = logging.getLogger(__name__)


def _time_range(granularity: str, now: datetime) -> tuple[datetime, datetime]:
    if granularity == "daily":
        return now - timedelta(days=1), now
    elif granularity == "weekly":
        return now - timedelta(weeks=1), now
    else:
        return now - timedelta(days=30), now


def _upsert_snapshot(
    db: Session,
    snapshot_date: str,
    granularity: str,
    node_id: int,
    mention_count: int,
    unique_users: int,
    unique_sessions: int,
    prev_count: int | None,
) -> None:
    growth_rate: float | None = None
    if prev_count is not None and prev_count > 0:
        growth_rate = (mention_count - prev_count) / prev_count

    existing = (
        db.query(KnowledgeSnapshot)
        .filter_by(snapshot_date=snapshot_date, granularity=granularity, node_id=node_id)
        .first()
    )
    if existing:
        existing.mention_count = mention_count
        existing.unique_users = unique_users
        existing.unique_sessions = unique_sessions
        existing.prev_mention_count = prev_count
        existing.growth_rate = growth_rate
    else:
        db.add(KnowledgeSnapshot(
            snapshot_date=snapshot_date,
            granularity=granularity,
            node_id=node_id,
            mention_count=mention_count,
            unique_users=unique_users,
            unique_sessions=unique_sessions,
            prev_mention_count=prev_count,
            growth_rate=growth_rate,
        ))


def _run_for_granularity(db: Session, granularity: str, now: datetime) -> int:
    start, end = _time_range(granularity, now)
    snapshot_date = now.strftime("%Y-%m-%d")

    rows = (
        db.query(
            KnowledgeMention.node_id,
            func.count().label("cnt"),
            func.count(func.distinct(KnowledgeMention.username)).label("users"),
            func.count(func.distinct(KnowledgeMention.session_id)).label("sessions"),
        )
        .filter(
            KnowledgeMention.mentioned_at >= start,
            KnowledgeMention.mentioned_at < end,
        )
        .group_by(KnowledgeMention.node_id)
        .all()
    )

    for row in rows:
        prev = (
            db.query(KnowledgeSnapshot)
            .filter(
                KnowledgeSnapshot.node_id == row.node_id,
                KnowledgeSnapshot.granularity == granularity,
                KnowledgeSnapshot.snapshot_date < snapshot_date,
            )
            .order_by(KnowledgeSnapshot.snapshot_date.desc())
            .first()
        )
        prev_count = prev.mention_count if prev else None
        _upsert_snapshot(db, snapshot_date, granularity, row.node_id,
                         row.cnt, row.users, row.sessions, prev_count)
    return len(rows)


def run_snapshot(db: Session, now: datetime | None = None) -> dict[str, int]:
    """all granularities에 대해 스냅샷 집계. 처리된 노드 수 반환."""
    if now is None:
        now = datetime.now(timezone.utc)

    result: dict[str, int] = {"daily": 0, "weekly": 0, "monthly": 0}
    result["daily"] = _run_for_granularity(db, "daily", now)
    if now.weekday() == 0:
        result["weekly"] = _run_for_granularity(db, "weekly", now)
    if now.day == 1:
        result["monthly"] = _run_for_granularity(db, "monthly", now)

    db.commit()
    logger.info(f"knowledge snapshot complete: {result}")
    return result
