"""Task 13 — parse_event: request_id, source, cache 토큰, model_id 필드 검증."""

import os
import sys
import uuid
from unittest.mock import MagicMock, patch

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")

with patch("sqlalchemy.create_engine", return_value=MagicMock()):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import worker  # noqa: E402


def _base_fields(**kwargs):
    f = {
        "username": "N1102359",
        "model": "global.anthropic.claude-sonnet-4-6",
        "input_tokens": "100",
        "output_tokens": "50",
        "total_tokens": "150",
        "cost_usd": "0.001",
        "cost_krw": "1",
        "recorded_at": "2026-04-25T10:00:00+00:00",
    }
    f.update(kwargs)
    return f


class TestParseEventRequestId:
    def test_request_id_passed_through(self):
        """fields에 request_id 있으면 그대로 반환."""
        rid = str(uuid.uuid4())
        ev = worker._parse_event(_base_fields(request_id=rid))
        assert ev is not None
        assert ev["request_id"] == rid

    def test_missing_request_id_generates_uuid(self):
        """fields에 request_id 없으면 uuid4 자동 생성."""
        ev = worker._parse_event(_base_fields())
        assert ev is not None
        assert "request_id" in ev
        # uuid4 형식 검증
        parsed = uuid.UUID(ev["request_id"])
        assert parsed.version == 4

    def test_empty_request_id_generates_uuid(self):
        """빈 문자열 request_id → uuid4 생성."""
        ev = worker._parse_event(_base_fields(request_id=""))
        assert ev is not None
        parsed = uuid.UUID(ev["request_id"])
        assert parsed.version == 4


class TestParseEventSource:
    def test_source_passed_through(self):
        """fields에 source 있으면 그대로 반환."""
        ev = worker._parse_event(_base_fields(source="console-cli"))
        assert ev is not None
        assert ev["source"] == "console-cli"

    def test_missing_source_defaults_to_legacy(self):
        """fields에 source 없으면 'legacy' 기본값."""
        ev = worker._parse_event(_base_fields())
        assert ev is not None
        assert ev["source"] == "legacy"

    def test_onlyoffice_source(self):
        """onlyoffice source 그대로 보존."""
        ev = worker._parse_event(_base_fields(source="onlyoffice"))
        assert ev is not None
        assert ev["source"] == "onlyoffice"


class TestParseEventCacheTokens:
    def test_cache_creation_tokens_extracted(self):
        """cache_creation_input_tokens 필드 추출."""
        ev = worker._parse_event(_base_fields(cache_creation_input_tokens="25"))
        assert ev is not None
        assert ev["cache_creation_input_tokens"] == 25

    def test_cache_read_tokens_extracted(self):
        """cache_read_input_tokens 필드 추출."""
        ev = worker._parse_event(_base_fields(cache_read_input_tokens="10"))
        assert ev is not None
        assert ev["cache_read_input_tokens"] == 10

    def test_missing_cache_tokens_default_zero(self):
        """cache 필드 없으면 0 기본값."""
        ev = worker._parse_event(_base_fields())
        assert ev is not None
        assert ev["cache_creation_input_tokens"] == 0
        assert ev["cache_read_input_tokens"] == 0

    def test_all_cache_fields_present(self):
        """cache_creation + cache_read 모두 있을 때 모두 추출."""
        ev = worker._parse_event(_base_fields(
            cache_creation_input_tokens="30",
            cache_read_input_tokens="15",
        ))
        assert ev is not None
        assert ev["cache_creation_input_tokens"] == 30
        assert ev["cache_read_input_tokens"] == 15


class TestParseEventModelId:
    def test_model_id_in_result(self):
        """model_id 필드가 반환 dict에 존재."""
        ev = worker._parse_event(_base_fields(model="global.anthropic.claude-haiku-4-5-20251001-v1:0"))
        assert ev is not None
        assert ev["model_id"] == "global.anthropic.claude-haiku-4-5-20251001-v1:0"

    def test_model_id_same_as_model(self):
        """model_id는 model 필드와 동일."""
        model = "global.anthropic.claude-sonnet-4-6"
        ev = worker._parse_event(_base_fields(model=model))
        assert ev is not None
        assert ev["model_id"] == model
        assert ev["model"] == model
