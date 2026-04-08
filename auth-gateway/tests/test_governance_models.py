"""Tests for file governance and audit log models."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models.file_audit import FileAuditAction, FileAuditLog
from app.models.file_governance import FileClassification, FileStatus, GovernedFile


# ---------------------------------------------------------------------------
# GovernedFile tests
# ---------------------------------------------------------------------------


class TestGovernedFilePersistence:
    def test_create_with_all_fields(self, db_session):
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=30)
        gf = GovernedFile(
            username="N1102359",
            filename="salary_2026.xlsx",
            file_path="/efs/N1102359/salary_2026.xlsx",
            file_type="xlsx",
            file_size_bytes=204800,
            classification=FileClassification.SENSITIVE,
            classification_reason="filename_match:salary",
            status=FileStatus.QUARANTINE,
            ttl_days=30,
            expires_at=expires,
            classified_at=now,
        )
        db_session.add(gf)
        db_session.commit()
        db_session.refresh(gf)

        assert gf.id is not None
        assert gf.username == "N1102359"
        assert gf.filename == "salary_2026.xlsx"
        assert gf.file_path == "/efs/N1102359/salary_2026.xlsx"
        assert gf.file_type == "xlsx"
        assert gf.file_size_bytes == 204800
        assert gf.classification == FileClassification.SENSITIVE
        assert gf.classification_reason == "filename_match:salary"
        assert gf.status == FileStatus.QUARANTINE
        assert gf.ttl_days == 30
        assert gf.created_at is not None

    def test_create_with_minimal_fields(self, db_session):
        gf = GovernedFile(
            username="N0000001",
            filename="report.pdf",
            file_path="/efs/N0000001/report.pdf",
        )
        db_session.add(gf)
        db_session.commit()
        db_session.refresh(gf)

        assert gf.id is not None
        assert gf.file_type is None
        assert gf.file_size_bytes == 0
        assert gf.classification == FileClassification.UNKNOWN
        assert gf.status == FileStatus.QUARANTINE
        assert gf.ttl_days is None
        assert gf.expires_at is None
        assert gf.classified_at is None

    def test_default_timestamps_set(self, db_session):
        gf = GovernedFile(
            username="N0000001",
            filename="data.csv",
            file_path="/efs/N0000001/data.csv",
        )
        db_session.add(gf)
        db_session.commit()
        db_session.refresh(gf)

        assert gf.created_at is not None
        assert gf.updated_at is not None

    def test_null_ttl_allowed(self, db_session):
        gf = GovernedFile(
            username="N0000002",
            filename="notes.txt",
            file_path="/efs/N0000002/notes.txt",
            ttl_days=None,
        )
        db_session.add(gf)
        db_session.commit()
        db_session.refresh(gf)

        assert gf.ttl_days is None

    def test_expires_at_calculation(self, db_session):
        """expires_at should equal created_at + ttl_days when set manually."""
        now = datetime.now(timezone.utc)
        ttl = 7
        expires = now + timedelta(days=ttl)

        gf = GovernedFile(
            username="N0000003",
            filename="temp.csv",
            file_path="/efs/N0000003/temp.csv",
            ttl_days=ttl,
            expires_at=expires,
        )
        db_session.add(gf)
        db_session.commit()
        db_session.refresh(gf)

        # Allow a small tolerance for datetime comparison
        delta = abs((gf.expires_at.replace(tzinfo=timezone.utc) - expires).total_seconds())
        assert delta < 2

    def test_username_index_supports_query(self, db_session):
        for i in range(3):
            db_session.add(GovernedFile(
                username="N9999999",
                filename=f"file_{i}.csv",
                file_path=f"/efs/N9999999/file_{i}.csv",
            ))
        db_session.add(GovernedFile(
            username="N0000000",
            filename="other.csv",
            file_path="/efs/N0000000/other.csv",
        ))
        db_session.commit()

        results = db_session.query(GovernedFile).filter_by(username="N9999999").all()
        assert len(results) == 3


# ---------------------------------------------------------------------------
# FileClassification enum tests
# ---------------------------------------------------------------------------


class TestFileClassificationEnum:
    def test_sensitive_value(self):
        assert FileClassification.SENSITIVE == "sensitive"

    def test_normal_value(self):
        assert FileClassification.NORMAL == "normal"

    def test_unknown_value(self):
        assert FileClassification.UNKNOWN == "unknown"

    def test_all_values(self):
        values = {e.value for e in FileClassification}
        assert values == {"sensitive", "normal", "unknown"}


# ---------------------------------------------------------------------------
# FileStatus enum tests
# ---------------------------------------------------------------------------


class TestFileStatusEnum:
    def test_active_value(self):
        assert FileStatus.ACTIVE == "active"

    def test_quarantine_value(self):
        assert FileStatus.QUARANTINE == "quarantine"

    def test_expired_value(self):
        assert FileStatus.EXPIRED == "expired"

    def test_deleted_value(self):
        assert FileStatus.DELETED == "deleted"

    def test_all_values(self):
        values = {e.value for e in FileStatus}
        assert values == {"active", "quarantine", "expired", "deleted"}


# ---------------------------------------------------------------------------
# FileAuditLog tests
# ---------------------------------------------------------------------------


class TestFileAuditLogPersistence:
    def test_create_with_all_fields(self, db_session):
        log = FileAuditLog(
            username="N1102359",
            action=FileAuditAction.UPLOAD,
            filename="data.csv",
            file_path="/efs/N1102359/data.csv",
            detail=json.dumps({"size_bytes": 1024, "mime": "text/csv"}),
            ip_address="192.168.1.10",
        )
        db_session.add(log)
        db_session.commit()
        db_session.refresh(log)

        assert log.id is not None
        assert log.username == "N1102359"
        assert log.action == FileAuditAction.UPLOAD
        assert log.filename == "data.csv"
        assert log.file_path == "/efs/N1102359/data.csv"
        assert log.ip_address == "192.168.1.10"
        assert log.created_at is not None
        parsed = json.loads(log.detail)
        assert parsed["size_bytes"] == 1024

    def test_create_minimal(self, db_session):
        log = FileAuditLog(
            username="N0000001",
            action=FileAuditAction.DELETE,
        )
        db_session.add(log)
        db_session.commit()
        db_session.refresh(log)

        assert log.id is not None
        assert log.filename is None
        assert log.file_path is None
        assert log.detail is None
        assert log.ip_address is None

    def test_ipv6_address_stored(self, db_session):
        ipv6 = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
        log = FileAuditLog(
            username="N0000002",
            action=FileAuditAction.ACCESS,
            ip_address=ipv6,
        )
        db_session.add(log)
        db_session.commit()
        db_session.refresh(log)

        assert log.ip_address == ipv6

    def test_multiple_actions_for_same_user(self, db_session):
        for action in [FileAuditAction.UPLOAD, FileAuditAction.CLASSIFY, FileAuditAction.QUARANTINE]:
            db_session.add(FileAuditLog(username="N8888888", action=action))
        db_session.commit()

        logs = db_session.query(FileAuditLog).filter_by(username="N8888888").all()
        assert len(logs) == 3
        actions = {log.action for log in logs}
        assert FileAuditAction.UPLOAD in actions
        assert FileAuditAction.CLASSIFY in actions
        assert FileAuditAction.QUARANTINE in actions


# ---------------------------------------------------------------------------
# FileAuditAction enum tests
# ---------------------------------------------------------------------------


class TestFileAuditActionEnum:
    def test_all_values(self):
        expected = {"upload", "classify", "delete", "share", "access", "quarantine", "expire", "extend"}
        values = {e.value for e in FileAuditAction}
        assert values == expected

    def test_string_comparison(self):
        assert FileAuditAction.UPLOAD == "upload"
        assert FileAuditAction.EXTEND == "extend"
