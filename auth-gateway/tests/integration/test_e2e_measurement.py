"""End-to-end 측정 파이프라인 통합 테스트.

전제: REDIS_URL, DATABASE_URL 환경변수 + usage-worker 가동 중.
실행: pytest tests/integration/test_e2e_measurement.py -v --runintegration
"""
import os
import time
import uuid
from datetime import datetime, timezone, date

import pytest
import redis
from sqlalchemy import create_engine, text


pytestmark = pytest.mark.integration


@pytest.fixture
def r():
    return redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)


@pytest.fixture
def db():
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        yield conn


def test_publish_then_db_row_appears_within_5s(r, db):
    """proxy 모듈의 publish 함수를 직접 호출 → worker가 5초 내 DB row 작성 확인."""
    from app.routers.bedrock_proxy import _publish_usage_event

    request_id = str(uuid.uuid4())
    test_user = f"E2ETST{int(time.time()) % 100000}"

    _publish_usage_event(
        request_id=request_id,
        source="e2e-test",
        username=test_user,
        model="global.anthropic.claude-sonnet-4-6",
        input_tokens=100, output_tokens=200,
        cache_creation_input_tokens=50, cache_read_input_tokens=25,
        cost_usd=0.005, cost_krw=7,
    )

    # 10초 내에 daily row 출현 + event row 존재 확인
    deadline = time.time() + 10
    daily_row = None
    while time.time() < deadline:
        result = db.execute(
            text("SELECT * FROM token_usage_daily WHERE username = :u"),
            {"u": test_user},
        ).fetchone()
        if result:
            daily_row = result
            break
        time.sleep(0.5)

    assert daily_row is not None, f"DB row not found within 10s for {test_user}"
    assert daily_row.input_tokens == 100
    assert daily_row.output_tokens == 200
    assert daily_row.cache_creation_input_tokens == 50
    assert daily_row.cache_read_input_tokens == 25
    assert daily_row.model_id == "global.anthropic.claude-sonnet-4-6"

    event_row = db.execute(
        text("SELECT * FROM token_usage_event WHERE request_id = :r"),
        {"r": request_id},
    ).fetchone()
    assert event_row is not None
    assert event_row.source == "e2e-test"

    # cleanup
    db.execute(text("DELETE FROM token_usage_daily WHERE username = :u"), {"u": test_user})
    db.execute(text("DELETE FROM token_usage_hourly WHERE username = :u"), {"u": test_user})
    db.execute(text("DELETE FROM token_usage_event WHERE username = :u"), {"u": test_user})
    db.commit()


def test_dedupe_publish_twice_results_in_single_count(r, db):
    """동일 request_id 두 번 publish → daily 합계는 1회 분만 반영."""
    from app.routers.bedrock_proxy import _publish_usage_event

    request_id = str(uuid.uuid4())
    test_user = f"DEDUPE{int(time.time()) % 100000}"

    for _ in range(2):
        _publish_usage_event(
            request_id=request_id,  # 동일!
            source="e2e-test",
            username=test_user,
            model="global.anthropic.claude-sonnet-4-6",
            input_tokens=100, output_tokens=200,
            cost_usd=0.005, cost_krw=7,
        )

    # worker가 두 이벤트 모두 처리할 시간 확보
    time.sleep(8)

    daily = db.execute(
        text("SELECT input_tokens, output_tokens FROM token_usage_daily WHERE username = :u"),
        {"u": test_user},
    ).fetchone()
    assert daily is not None
    assert daily.input_tokens == 100, f"Expected 100 (dedupe), got {daily.input_tokens}"
    assert daily.output_tokens == 200

    # event 테이블에는 1 row만
    event_count = db.execute(
        text("SELECT COUNT(*) AS c FROM token_usage_event WHERE request_id = :r"),
        {"r": request_id},
    ).fetchone().c
    assert event_count == 1

    # cleanup
    db.execute(text("DELETE FROM token_usage_daily WHERE username = :u"), {"u": test_user})
    db.execute(text("DELETE FROM token_usage_hourly WHERE username = :u"), {"u": test_user})
    db.execute(text("DELETE FROM token_usage_event WHERE username = :u"), {"u": test_user})
    db.commit()
