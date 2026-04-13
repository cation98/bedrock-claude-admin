"""Phase 1a: ops/export/skills.py — skills CSV export."""
import csv
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ops.export.skills import export_skills_to_csv


def test_export_skills_approved_only(tmp_path):
    # SimpleNamespace used instead of MagicMock because MagicMock(name=...) sets
    # the mock's internal name attribute, not the .name data attribute.
    rows = [
        SimpleNamespace(id="s1", name="Code Review",
                        approval_status="approved", author="u1"),
    ]
    with patch("ops.export.skills._fetch_skills", return_value=rows), \
         patch("ops.export.skills.db_session") as mock_session:
        mock_session.return_value.__enter__.return_value = MagicMock()
        output = tmp_path / "skills.csv"
        count = export_skills_to_csv(approval_status="approved", output_path=str(output))

    assert count == 1
    with output.open() as f:
        reader = csv.DictReader(f)
        result = list(reader)
    assert len(result) == 1
    assert result[0]["name"] == "Code Review"
    assert result[0]["approval_status"] == "approved"


def test_export_skills_header_present_when_empty(tmp_path):
    with patch("ops.export.skills._fetch_skills", return_value=[]), \
         patch("ops.export.skills.db_session") as mock_session:
        mock_session.return_value.__enter__.return_value = MagicMock()
        output = tmp_path / "empty.csv"
        count = export_skills_to_csv(approval_status="approved", output_path=str(output))

    assert count == 0
    with output.open() as f:
        first_line = f.readline().strip()
    assert first_line == "id,name,approval_status,author"
