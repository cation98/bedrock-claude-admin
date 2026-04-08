"""Tests for the file_scanner service.

Covers:
- classify_file: filename patterns, team-based, PII content, large file, normal
- extract_text_content: CSV extraction only (XLSX/DOCX not tested — optional deps)
- Classification priority: filename match wins over content
"""

import os
import tempfile
import pytest

from app.services.file_scanner import (
    classify_file,
    extract_text_content,
    MAX_CONTENT_SCAN_SIZE,
)


# ── classify_file tests ──────────────────────────────────────────────────────

def test_sensitive_filename_salary():
    """'salary_2026.csv' should be classified as sensitive due to filename."""
    result = classify_file(
        filename="salary_2026.csv",
        file_path="/uploads/salary_2026.csv",
    )
    assert result.classification == "sensitive"
    assert "filename_match" in result.reason


def test_sensitive_filename_korean():
    """'인사평가_결과.xlsx' should be classified as sensitive (Korean HR keyword)."""
    result = classify_file(
        filename="인사평가_결과.xlsx",
        file_path="/uploads/인사평가_결과.xlsx",
    )
    assert result.classification == "sensitive"
    assert "filename_match" in result.reason


def test_sensitive_filename_hr():
    """'hr_records.db' should be classified as sensitive (hr_ prefix pattern)."""
    result = classify_file(
        filename="hr_records.db",
        file_path="/uploads/hr_records.db",
    )
    assert result.classification == "sensitive"
    assert "filename_match" in result.reason


def test_normal_filename():
    """'meeting_notes.docx' has no sensitive patterns → should be normal."""
    result = classify_file(
        filename="meeting_notes.docx",
        file_path="/uploads/meeting_notes.docx",
    )
    assert result.classification == "normal"


def test_sensitive_team():
    """Normal filename but uploaded by HR팀 → sensitive due to team match."""
    result = classify_file(
        filename="meeting_notes.docx",
        file_path="/uploads/meeting_notes.docx",
        team_name="HR팀",
    )
    assert result.classification == "sensitive"
    assert "team_match" in result.reason
    assert "HR팀" in result.reason


def test_pii_jumin():
    """Content containing a Korean national ID (주민등록번호) → sensitive."""
    content = "이름: 홍길동, 주민번호: 900101-1234567"
    result = classify_file(
        filename="report.txt",
        file_path="/uploads/report.txt",
        file_size=len(content),
        content=content,
    )
    assert result.classification == "sensitive"
    assert "pii_match" in result.reason


def test_pii_phone():
    """Content containing a Korean mobile number → sensitive."""
    content = "연락처: 010-1234-5678"
    result = classify_file(
        filename="contacts.txt",
        file_path="/uploads/contacts.txt",
        file_size=len(content),
        content=content,
    )
    assert result.classification == "sensitive"
    assert "pii_match" in result.reason


def test_large_file_unknown():
    """File larger than 50 MB without filename match → unknown (too large to scan)."""
    large_size = MAX_CONTENT_SCAN_SIZE + 1  # 50 MB + 1 byte
    result = classify_file(
        filename="bigdata.csv",
        file_path="/uploads/bigdata.csv",
        file_size=large_size,
        # no content provided, no sensitive filename/team
    )
    assert result.classification == "unknown"
    assert "too_large" in result.reason


def test_no_content_normal():
    """File with no patterns, no team, no content → normal."""
    result = classify_file(
        filename="readme.txt",
        file_path="/uploads/readme.txt",
        file_size=100,
        content=None,
    )
    assert result.classification == "normal"
    assert result.reason == "no_sensitive_patterns_found"


def test_classification_priority_filename_over_content():
    """Filename match must take priority — even if content has no PII, sensitive wins."""
    # salary_ in filename triggers classification before content is checked
    result = classify_file(
        filename="salary_q1.csv",
        file_path="/uploads/salary_q1.csv",
        file_size=50,
        team_name=None,
        content="project,budget\ninfra,10000\n",  # no PII in content
    )
    assert result.classification == "sensitive"
    assert "filename_match" in result.reason


# ── extract_text_content tests ───────────────────────────────────────────────

def test_extract_csv():
    """extract_text_content should return the full text of a small CSV file."""
    csv_data = "name,age,department\n홍길동,30,개발팀\n김철수,25,마케팅팀\n"
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.csv', encoding='utf-8', delete=False
    ) as f:
        f.write(csv_data)
        tmp_path = f.name

    try:
        text = extract_text_content(tmp_path)
        assert text is not None
        assert "홍길동" in text
        assert "개발팀" in text
    finally:
        os.unlink(tmp_path)


def test_extract_txt():
    """extract_text_content should return text from a .txt file."""
    txt_data = "This is a test document.\n주민번호: 900101-1234567\n"
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.txt', encoding='utf-8', delete=False
    ) as f:
        f.write(txt_data)
        tmp_path = f.name

    try:
        text = extract_text_content(tmp_path)
        assert text is not None
        assert "900101-1234567" in text
    finally:
        os.unlink(tmp_path)


def test_extract_unknown_extension_returns_none():
    """extract_text_content returns None for unsupported binary extensions (e.g., .hwp)."""
    with tempfile.NamedTemporaryFile(
        mode='wb', suffix='.hwp', delete=False
    ) as f:
        f.write(b"\x00\x01\x02binary data")
        tmp_path = f.name

    try:
        text = extract_text_content(tmp_path)
        assert text is None
    finally:
        os.unlink(tmp_path)


def test_extract_oversized_file_returns_none():
    """extract_text_content returns None when file exceeds max_size."""
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.txt', encoding='utf-8', delete=False
    ) as f:
        f.write("hello world")
        tmp_path = f.name

    try:
        # Pass max_size=1 so the file (>1 byte) is treated as too large
        text = extract_text_content(tmp_path, max_size=1)
        assert text is None
    finally:
        os.unlink(tmp_path)
