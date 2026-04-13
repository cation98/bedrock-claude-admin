"""Phase 1a: ops/export/chats.py — Open WebUI chat 90일 JSONL export."""
import json
from unittest.mock import MagicMock, patch

from ops.export.chats import export_chats_to_jsonl


def test_export_chats_basic_format(tmp_path):
    """90일 이내 chat 2건 → JSONL 2 lines."""
    mock_rows = [
        MagicMock(
            id="c1", user_id="u1", created_at="2026-04-01T00:00:00Z",
            title="test1", message_count=5,
        ),
        MagicMock(
            id="c2", user_id="u1", created_at="2026-03-15T00:00:00Z",
            title="test2", message_count=2,
        ),
    ]
    with patch("ops.export.chats._fetch_chats", return_value=mock_rows), \
         patch("ops.export.chats.db_session") as mock_session:
        mock_session.return_value.__enter__.return_value = MagicMock()
        output = tmp_path / "chats.jsonl"
        count = export_chats_to_jsonl(user_id="u1", since_days=90, output_path=str(output))

    assert count == 2
    lines = output.read_text().strip().split("\n")
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["id"] == "c1"
    assert first["user_id"] == "u1"
    assert first["message_count"] == 5


def test_export_chats_empty_for_unknown_user(tmp_path):
    with patch("ops.export.chats._fetch_chats", return_value=[]), \
         patch("ops.export.chats.db_session") as mock_session:
        mock_session.return_value.__enter__.return_value = MagicMock()
        output = tmp_path / "chats.jsonl"
        count = export_chats_to_jsonl(
            user_id="unknown", since_days=90, output_path=str(output),
        )
    assert count == 0
    assert output.read_text() == ""


def test_export_chats_respects_since_days(tmp_path):
    """since_days=7 전달 시 _fetch_chats kwargs에 반영."""
    captured = {}

    def _capture(*, user_id, since_days, session):
        captured["user_id"] = user_id
        captured["since_days"] = since_days
        return []

    with patch("ops.export.chats._fetch_chats", side_effect=_capture), \
         patch("ops.export.chats.db_session") as mock_session:
        mock_session.return_value.__enter__.return_value = MagicMock()
        output = tmp_path / "x.jsonl"
        export_chats_to_jsonl(user_id="u1", since_days=7, output_path=str(output))

    assert captured == {"user_id": "u1", "since_days": 7}
