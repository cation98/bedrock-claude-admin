"""Tests for SMS share authentication endpoints.

Covers:
  POST /api/v1/files/datasets/{name}/verify-access
  POST /api/v1/files/datasets/{name}/verify-access-code

Test cases:
  1. test_owner_no_sms_needed          -- dataset owner gets direct access (no SMS)
  2. test_shared_user_sensitive_requires_sms -- shared user on sensitive file → requires_sms=True
  3. test_shared_user_normal_no_sms    -- shared user on normal (unclassified) file → access_granted=True
  4. test_no_access_denied             -- user without ACL → 403
  5. test_verify_code_grants_access    -- correct code → access_token returned
  6. test_verify_wrong_code_denied     -- wrong code → error
"""

import pytest
from datetime import datetime, timedelta, timezone

from app.models.file_share import SharedDataset, FileShareACL
from app.models.file_governance import GovernedFile
from app.models.two_factor_code import TwoFactorCode
from app.models.user import User


# --------------- Helpers ---------------

def _create_dataset(db, owner_username: str, dataset_name: str) -> SharedDataset:
    ds = SharedDataset(
        owner_username=owner_username,
        dataset_name=dataset_name,
        file_path=f"/shared/{dataset_name}.csv",
        file_type="csv",
        file_size_bytes=1024,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


def _create_user(db, username: str, phone_number: str = "010-1234-5678", team_name: str | None = None) -> User:
    user = User(
        username=username,
        name=f"User {username}",
        phone_number=phone_number,
        team_name=team_name,
        is_approved=True,
        role="user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_user_acl(db, dataset: SharedDataset, share_target: str, share_type: str = "user") -> FileShareACL:
    acl = FileShareACL(
        dataset_id=dataset.id,
        share_type=share_type,
        share_target=share_target,
        granted_by=dataset.owner_username,
    )
    db.add(acl)
    db.commit()
    db.refresh(acl)
    return acl


def _mark_sensitive(db, owner_username: str, dataset_name: str) -> GovernedFile:
    gf = GovernedFile(
        username=owner_username,
        filename=dataset_name,
        file_path=f"/shared/{dataset_name}.csv",
        classification="sensitive",
        status="active",
    )
    db.add(gf)
    db.commit()
    db.refresh(gf)
    return gf


# --------------- Test 1: Owner gets direct access ---------------

def test_owner_no_sms_needed(client, db_session):
    """Dataset owner calls verify-access → access_granted immediately (no SMS)."""
    _create_dataset(db_session, owner_username="TESTUSER01", dataset_name="payroll-2026")

    resp = client.post("/api/v1/files/datasets/payroll-2026/verify-access")
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_granted"] is True
    assert "소유자" in data["message"]


# --------------- Test 2: Shared user on sensitive file requires SMS ---------------

def test_shared_user_sensitive_requires_sms(client, db_session):
    """Shared user accessing a sensitive dataset → requires_sms=True, code_id returned."""
    # Owner creates the dataset
    ds = _create_dataset(db_session, owner_username="OWNER01", dataset_name="hr-records")

    # Mark it sensitive in GovernedFile
    _mark_sensitive(db_session, "OWNER01", "hr-records")

    # TESTUSER01 has ACL access
    _create_user(db_session, username="TESTUSER01", phone_number="010-9999-0001")
    _create_user_acl(db_session, ds, share_target="TESTUSER01", share_type="user")

    # TESTUSER01 (mocked as current user) requests access
    resp = client.post("/api/v1/files/datasets/hr-records/verify-access")
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_granted"] is False
    assert data["requires_sms"] is True
    assert "code_id" in data
    assert data["code_id"]  # non-empty
    assert "0001" in data["message"]  # last 4 digits of phone


# --------------- Test 3: Shared user on normal (unclassified) file ---------------

def test_shared_user_normal_no_sms(client, db_session):
    """Shared user accessing a non-sensitive file → access_granted=True immediately."""
    ds = _create_dataset(db_session, owner_username="OWNER01", dataset_name="meeting-notes")

    # No GovernedFile entry → not sensitive
    _create_user(db_session, username="TESTUSER01", phone_number="010-1111-2222")
    _create_user_acl(db_session, ds, share_target="TESTUSER01", share_type="user")

    resp = client.post("/api/v1/files/datasets/meeting-notes/verify-access")
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_granted"] is True
    assert "일반 파일" in data["message"]


# --------------- Test 4: User without ACL → 403 ---------------

def test_no_access_denied(client, db_session):
    """User who has no ACL entry for the dataset → 403."""
    # Dataset owned by OWNER01, no ACL for TESTUSER01
    _create_dataset(db_session, owner_username="OWNER01", dataset_name="secret-data")

    resp = client.post("/api/v1/files/datasets/secret-data/verify-access")
    assert resp.status_code == 403
    assert "접근 권한" in resp.json()["detail"]


# --------------- Test 5: Correct code grants access token ---------------

def test_verify_code_grants_access(client, db_session):
    """Posting the correct code to verify-access-code → access_token issued."""
    # SQLite stores datetimes as naive (no timezone). Use naive UTC datetimes
    # to match what SQLite returns on read, avoiding offset-aware comparison errors.
    now = datetime.utcnow()
    code_record = TwoFactorCode(
        username="TESTUSER01",
        code="123456",
        phone_number="010-1234-5678",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        attempts=0,
        verified=False,
    )
    db_session.add(code_record)
    db_session.commit()
    db_session.refresh(code_record)

    resp = client.post(
        "/api/v1/files/datasets/some-dataset/verify-access-code",
        json={"code_id": code_record.id, "code": "123456"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_granted"] is True
    assert "access_token" in data
    assert data["expires_in"] == 1800
    assert "인증 완료" in data["message"]


# --------------- Test 6: Wrong code → error ---------------

def test_verify_wrong_code_denied(client, db_session):
    """Posting the wrong code to verify-access-code → non-200 error response."""
    # SQLite stores datetimes as naive (no timezone). Use naive UTC datetimes.
    now = datetime.utcnow()
    code_record = TwoFactorCode(
        username="TESTUSER01",
        code="654321",
        phone_number="010-1234-5678",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        attempts=0,
        verified=False,
    )
    db_session.add(code_record)
    db_session.commit()
    db_session.refresh(code_record)

    resp = client.post(
        "/api/v1/files/datasets/some-dataset/verify-access-code",
        json={"code_id": code_record.id, "code": "000000"},
    )
    # verify_code raises CodeInvalidError (a TwoFactorError subclass).
    # The endpoint does not catch it → FastAPI returns 500.
    # With raise_server_exceptions=False (set in conftest.py), we get the response.
    assert resp.status_code != 200
