"""Tests for the file governance API.

Covers:
1. test_scan_report_classifies_files    -- POST scan-report → correct classification
2. test_scan_report_sets_ttl            -- sensitive=7일, normal=30일 TTL
3. test_scan_report_upsert              -- 동일 파일 재보고 → 업데이트 (중복 없음)
4. test_dashboard_returns_stats         -- admin이 올바른 통계 수신
5. test_dashboard_non_admin_forbidden   -- 비관리자 → 403
6. test_files_list_with_filters         -- classification 필터 동작
7. test_files_pagination                -- page/per_page 동작
"""

import pytest
from datetime import datetime, timedelta, timezone

from app.models.file_governance import GovernedFile
from app.models.file_audit import FileAuditLog


# ==================== Helpers ====================

_SCAN_REPORT_URL = "/api/v1/governance/scan-report"
_DASHBOARD_URL = "/api/v1/governance/dashboard"
_FILES_URL = "/api/v1/governance/files"

_SENSITIVE_FILE = {
    "filename": "salary_2026.csv",
    "file_path": "/workspace/salary_2026.csv",
    "file_size_bytes": 1024,
    "file_type": "csv",
}

_NORMAL_FILE = {
    "filename": "meeting_notes.txt",
    "file_path": "/workspace/meeting_notes.txt",
    "file_size_bytes": 512,
    "file_type": "txt",
}

_SCAN_PAYLOAD = {
    "pod_name": "claude-terminal-testuser01",
    "files": [_SENSITIVE_FILE, _NORMAL_FILE],
}


# ==================== Tests ====================


def test_scan_report_classifies_files(client, db_session):
    """POST /scan-report classifies sensitive and normal files correctly."""
    response = client.post(_SCAN_REPORT_URL, json=_SCAN_PAYLOAD)
    assert response.status_code == 200, response.text

    data = response.json()
    assert data["classified"] == 2
    assert data["sensitive"] == 1
    assert data["normal"] == 1

    # Verify DB records
    files = db_session.query(GovernedFile).all()
    assert len(files) == 2

    sensitive_file = next(f for f in files if f.filename == "salary_2026.csv")
    normal_file = next(f for f in files if f.filename == "meeting_notes.txt")

    assert sensitive_file.classification == "sensitive"
    assert normal_file.classification == "normal"
    assert sensitive_file.status == "active"
    assert normal_file.status == "active"


def test_scan_report_sets_ttl(client, db_session):
    """Sensitive file gets 7-day TTL, normal file gets 30-day TTL."""
    client.post(_SCAN_REPORT_URL, json=_SCAN_PAYLOAD)

    files = db_session.query(GovernedFile).all()
    sensitive_file = next(f for f in files if f.filename == "salary_2026.csv")
    normal_file = next(f for f in files if f.filename == "meeting_notes.txt")

    assert sensitive_file.ttl_days == 7
    assert normal_file.ttl_days == 30

    # expires_at should be ~7 / ~30 days from now
    # SQLite stores datetimes as naive (no timezone); strip tz for comparison.
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    sensitive_expires = (
        sensitive_file.expires_at.replace(tzinfo=None)
        if sensitive_file.expires_at.tzinfo is not None
        else sensitive_file.expires_at
    )
    normal_expires = (
        normal_file.expires_at.replace(tzinfo=None)
        if normal_file.expires_at.tzinfo is not None
        else normal_file.expires_at
    )

    sensitive_delta = (sensitive_expires - now).total_seconds() / 86400
    assert 6.5 < sensitive_delta < 7.5, f"Expected ~7 days, got {sensitive_delta:.2f}"

    normal_delta = (normal_expires - now).total_seconds() / 86400
    assert 29.5 < normal_delta < 30.5, f"Expected ~30 days, got {normal_delta:.2f}"


def test_scan_report_upsert(client, db_session):
    """Reporting the same file twice updates rather than duplicating."""
    # First report
    client.post(_SCAN_REPORT_URL, json=_SCAN_PAYLOAD)
    count_after_first = db_session.query(GovernedFile).count()

    # Second report with same files
    client.post(_SCAN_REPORT_URL, json=_SCAN_PAYLOAD)
    count_after_second = db_session.query(GovernedFile).count()

    assert count_after_first == count_after_second, (
        f"Expected upsert (no duplicates), but count changed: "
        f"{count_after_first} → {count_after_second}"
    )

    # Should have updated classified_at on the second call
    files = db_session.query(GovernedFile).all()
    for f in files:
        assert f.classified_at is not None


def test_dashboard_returns_stats(client, db_session):
    """Admin user gets correct dashboard stats."""
    from app.core.security import get_current_user
    from tests.conftest import _test_app

    # Insert governed files directly
    now = datetime.now(timezone.utc)

    active_sensitive = GovernedFile(
        username="TESTUSER01",
        filename="salary.csv",
        file_path="/workspace/salary.csv",
        classification="sensitive",
        status="active",
        ttl_days=7,
        expires_at=now + timedelta(days=5),  # expiring within 7 days
        file_size_bytes=2048,
    )
    active_normal = GovernedFile(
        username="TESTUSER01",
        filename="notes.txt",
        file_path="/workspace/notes.txt",
        classification="normal",
        status="active",
        ttl_days=30,
        expires_at=now + timedelta(days=25),
        file_size_bytes=1024,
    )
    expired_file = GovernedFile(
        username="TESTUSER01",
        filename="old.txt",
        file_path="/workspace/old.txt",
        classification="normal",
        status="expired",
        ttl_days=30,
        expires_at=now - timedelta(days=1),
        file_size_bytes=512,
    )
    db_session.add_all([active_sensitive, active_normal, expired_file])
    db_session.commit()

    # Override to admin user
    def _admin_user():
        return {"sub": "ADMIN01", "role": "admin", "name": "Admin"}

    _test_app.dependency_overrides[get_current_user] = _admin_user

    try:
        response = client.get(_DASHBOARD_URL)
        assert response.status_code == 200, response.text

        data = response.json()
        assert data["total_files"] == 2          # only active files
        assert data["sensitive_files"] == 1
        assert data["expiring_soon"] == 1        # expires in 5 days (within 7)
        assert data["storage_used_bytes"] == 3072  # 2048 + 1024
    finally:
        # Restore default mock
        from tests.conftest import _mock_current_user
        _test_app.dependency_overrides[get_current_user] = _mock_current_user


def test_dashboard_non_admin_forbidden(client):
    """Non-admin user should get 403 on dashboard endpoint."""
    # Default client uses role="user"
    response = client.get(_DASHBOARD_URL)
    assert response.status_code == 403, response.text


def test_files_list_with_filters(client, db_session):
    """GET /files with classification filter returns only matching files."""
    from app.core.security import get_current_user
    from tests.conftest import _test_app

    now = datetime.now(timezone.utc)
    db_session.add_all([
        GovernedFile(
            username="U1",
            filename="salary.csv",
            file_path="/workspace/salary.csv",
            classification="sensitive",
            status="active",
            ttl_days=7,
            expires_at=now + timedelta(days=7),
        ),
        GovernedFile(
            username="U1",
            filename="notes.txt",
            file_path="/workspace/notes.txt",
            classification="normal",
            status="active",
            ttl_days=30,
            expires_at=now + timedelta(days=30),
        ),
        GovernedFile(
            username="U2",
            filename="hr_data.xlsx",
            file_path="/workspace/hr_data.xlsx",
            classification="sensitive",
            status="active",
            ttl_days=7,
            expires_at=now + timedelta(days=7),
        ),
    ])
    db_session.commit()

    def _admin_user():
        return {"sub": "ADMIN01", "role": "admin"}

    _test_app.dependency_overrides[get_current_user] = _admin_user

    try:
        # Filter by classification=sensitive
        response = client.get(_FILES_URL, params={"classification": "sensitive"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 2
        for f in data["files"]:
            assert f["classification"] == "sensitive"

        # Filter by classification=normal
        response = client.get(_FILES_URL, params={"classification": "normal"})
        data = response.json()
        assert data["total"] == 1
        assert data["files"][0]["classification"] == "normal"

        # Filter by username
        response = client.get(_FILES_URL, params={"username": "U2"})
        data = response.json()
        assert data["total"] == 1
        assert data["files"][0]["username"] == "U2"
    finally:
        from tests.conftest import _mock_current_user
        _test_app.dependency_overrides[get_current_user] = _mock_current_user


def test_files_pagination(client, db_session):
    """GET /files page/per_page pagination works correctly."""
    from app.core.security import get_current_user
    from tests.conftest import _test_app

    now = datetime.now(timezone.utc)
    # Insert 5 files
    for i in range(5):
        db_session.add(GovernedFile(
            username="U1",
            filename=f"file_{i}.txt",
            file_path=f"/workspace/file_{i}.txt",
            classification="normal",
            status="active",
            ttl_days=30,
            expires_at=now + timedelta(days=30),
        ))
    db_session.commit()

    def _admin_user():
        return {"sub": "ADMIN01", "role": "admin"}

    _test_app.dependency_overrides[get_current_user] = _admin_user

    try:
        # First page of 2
        response = client.get(_FILES_URL, params={"page": 1, "per_page": 2})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 5
        assert len(data["files"]) == 2
        assert data["page"] == 1
        assert data["per_page"] == 2

        # Second page of 2
        response = client.get(_FILES_URL, params={"page": 2, "per_page": 2})
        data = response.json()
        assert len(data["files"]) == 2

        # Third page (1 remaining)
        response = client.get(_FILES_URL, params={"page": 3, "per_page": 2})
        data = response.json()
        assert len(data["files"]) == 1
    finally:
        from tests.conftest import _mock_current_user
        _test_app.dependency_overrides[get_current_user] = _mock_current_user
