"""Phase 1a: ops/export/audit.py — FileAuditLog JSONL + PII masking."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ops.export.audit import export_audit_to_jsonl


def test_export_audit_masks_email(tmp_path):
    rows = [
        SimpleNamespace(
            id=1,
            user_email="alice@skons.net",
            file_path="/safe/path",
            action="view",
            created_at="2026-04-01T00:00:00Z",
        ),
    ]
    with patch("ops.export.audit._fetch_audit", return_value=rows), \
         patch("ops.export.audit.db_session") as mock_session:
        mock_session.return_value.__enter__.return_value = MagicMock()
        output = tmp_path / "audit.jsonl"
        count = export_audit_to_jsonl(since_days=30, output_path=str(output))

    assert count == 1
    record = json.loads(output.read_text().strip())
    assert record["user_email"] == "a***@skons.net"  # PII masked
    assert record["file_path"] == "/safe/path"
    assert record["action"] == "view"


def test_export_audit_respects_since_days(tmp_path):
    captured = {}

    def _capture(*, since_days, session):
        captured["since_days"] = since_days
        return []

    with patch("ops.export.audit._fetch_audit", side_effect=_capture), \
         patch("ops.export.audit.db_session") as mock_session:
        mock_session.return_value.__enter__.return_value = MagicMock()
        output = tmp_path / "x.jsonl"
        export_audit_to_jsonl(since_days=7, output_path=str(output))

    assert captured["since_days"] == 7


def test_export_audit_null_email_passthrough(tmp_path):
    """user_email이 None이면 마스킹 없이 None 유지."""
    rows = [
        SimpleNamespace(
            id=2,
            user_email=None,
            file_path="/another/path",
            action="download",
            created_at="2026-04-02T00:00:00Z",
        ),
    ]
    with patch("ops.export.audit._fetch_audit", return_value=rows), \
         patch("ops.export.audit.db_session") as mock_session:
        mock_session.return_value.__enter__.return_value = MagicMock()
        output = tmp_path / "null.jsonl"
        count = export_audit_to_jsonl(since_days=30, output_path=str(output))

    assert count == 1
    record = json.loads(output.read_text().strip())
    assert record["user_email"] is None
