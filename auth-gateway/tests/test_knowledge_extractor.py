import json
from unittest.mock import MagicMock

from app.services.knowledge_extractor import (
    normalize_name,
    parse_extraction_response,
    group_conversations_by_session,
)


def test_normalize_name_lowercases_and_strips():
    assert normalize_name("  Python Pandas  ") == "python pandas"


def test_normalize_name_removes_special_chars():
    assert normalize_name("Docker-컨테이너(최적화)") == "docker 컨테이너 최적화"


def test_parse_extraction_response_valid():
    raw = json.dumps({
        "concepts": [
            {"name": "Python pandas", "type": "tool", "confidence": 0.9},
            {"name": "데이터 시각화", "type": "skill", "confidence": 0.8},
        ],
        "relationships": [
            {"source": "Python pandas", "target": "데이터 시각화", "type": "co_occurs"}
        ],
    })
    result = parse_extraction_response(raw)
    assert len(result["concepts"]) == 2
    assert result["concepts"][0]["name"] == "Python pandas"
    assert len(result["relationships"]) == 1


def test_parse_extraction_response_invalid_json_returns_empty():
    result = parse_extraction_response("not json")
    assert result == {"concepts": [], "relationships": []}


def test_parse_extraction_response_missing_keys_returns_empty():
    result = parse_extraction_response(json.dumps({"other": "data"}))
    assert result["concepts"] == []
    assert result["relationships"] == []


def test_group_conversations_by_session():
    convs = [
        MagicMock(session_id="s1", content="hello", username="u1"),
        MagicMock(session_id="s1", content="world", username="u1"),
        MagicMock(session_id="s2", content="foo", username="u2"),
    ]
    groups = group_conversations_by_session(convs)
    assert len(groups) == 2
    assert len(groups["s1"]) == 2
    assert len(groups["s2"]) == 1
