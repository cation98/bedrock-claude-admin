import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator, Text

from app.core.database import Base
from app.models.knowledge import KnowledgeNode, KnowledgeMention, KnowledgeSnapshot  # noqa: F401
from app.models.prompt_audit import PromptAuditConversation, PromptAuditSummary, PromptAuditFlag  # noqa: F401

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


class _JSONBtoText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(value) if value is not None and not isinstance(value, str) else value

    def process_result_value(self, value, dialect):
        if value is not None and isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value


for _table in Base.metadata.tables.values():
    for _col in _table.columns:
        if isinstance(_col.type, JSONB):
            _col.type = _JSONBtoText()

TestSession = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def _tables():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def test_run_snapshot_no_mentions_returns_zeros(db):
    from app.services.knowledge_snapshot import run_snapshot
    result = run_snapshot(db, now=datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc))
    assert result == {"daily": 0, "weekly": 0, "monthly": 0}


def test_run_snapshot_creates_daily_entry(db):
    from app.services.knowledge_snapshot import run_snapshot
    node = KnowledgeNode(concept_name="Python", concept_type="tool", normalized_name="python")
    db.add(node)
    db.flush()

    now = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)
    db.add(KnowledgeMention(
        conversation_id=1,
        node_id=node.id,
        username="USER01",
        session_id="sess1",
        mentioned_at=now - timedelta(hours=1),
    ))
    db.commit()

    result = run_snapshot(db, now=now)

    assert result["daily"] == 1
    snap = db.query(KnowledgeSnapshot).filter_by(granularity="daily", node_id=node.id).first()
    assert snap is not None
    assert snap.mention_count == 1
    assert snap.unique_users == 1


def test_run_snapshot_weekly_skipped_on_non_monday(db):
    from app.services.knowledge_snapshot import run_snapshot
    node = KnowledgeNode(concept_name="X", concept_type="tool", normalized_name="x")
    db.add(node)
    db.flush()
    now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)  # Thursday
    db.add(KnowledgeMention(conversation_id=1, node_id=node.id, username="U1",
                            mentioned_at=now - timedelta(hours=1)))
    db.commit()
    result = run_snapshot(db, now=now)
    assert result["weekly"] == 0


def test_run_snapshot_growth_rate(db):
    from app.services.knowledge_snapshot import run_snapshot
    node = KnowledgeNode(concept_name="Docker", concept_type="tool", normalized_name="docker")
    db.add(node)
    db.flush()
    now = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)
    db.add(KnowledgeSnapshot(
        snapshot_date="2026-04-23",
        granularity="daily",
        node_id=node.id,
        mention_count=10,
    ))
    for i in range(15):
        db.add(KnowledgeMention(
            conversation_id=i + 1,
            node_id=node.id,
            username="U1",
            mentioned_at=now - timedelta(hours=i % 12 + 1),
        ))
    db.commit()
    run_snapshot(db, now=now)
    snap = db.query(KnowledgeSnapshot).filter_by(
        granularity="daily", node_id=node.id, snapshot_date="2026-04-24"
    ).first()
    assert snap.growth_rate == pytest.approx(0.5, rel=1e-3)


def test_run_snapshot_monthly_only_on_first(db):
    from app.services.knowledge_snapshot import run_snapshot
    node = KnowledgeNode(concept_name="K8s", concept_type="tool", normalized_name="k8s")
    db.add(node)
    db.flush()
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)  # 15th, not 1st
    db.add(KnowledgeMention(conversation_id=1, node_id=node.id, username="U1",
                            mentioned_at=now - timedelta(hours=1)))
    db.commit()
    result = run_snapshot(db, now=now)
    assert result["monthly"] == 0


def test_run_snapshot_weekly_runs_on_monday(db):
    from app.services.knowledge_snapshot import run_snapshot
    node = KnowledgeNode(concept_name="Weekly", concept_type="tool", normalized_name="weekly")
    db.add(node)
    db.flush()
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)  # Monday
    db.add(KnowledgeMention(conversation_id=1, node_id=node.id, username="U1",
                            mentioned_at=now - timedelta(days=3)))
    db.commit()
    result = run_snapshot(db, now=now)
    assert result["weekly"] == 1
    snap = db.query(KnowledgeSnapshot).filter_by(granularity="weekly", node_id=node.id).first()
    assert snap is not None


def test_run_snapshot_monthly_runs_on_first(db):
    from app.services.knowledge_snapshot import run_snapshot
    node = KnowledgeNode(concept_name="Monthly", concept_type="tool", normalized_name="monthly")
    db.add(node)
    db.flush()
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)  # 1st of month
    db.add(KnowledgeMention(conversation_id=1, node_id=node.id, username="U1",
                            mentioned_at=now - timedelta(days=15)))
    db.commit()
    result = run_snapshot(db, now=now)
    assert result["monthly"] == 1
    snap = db.query(KnowledgeSnapshot).filter_by(granularity="monthly", node_id=node.id).first()
    assert snap is not None
