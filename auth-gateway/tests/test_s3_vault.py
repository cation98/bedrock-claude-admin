"""Tests for S3 Vault service and secure-files API.

Covers:
1. test_upload_to_vault               -- upload file → returns vault_id
2. test_download_from_vault           -- download returns file content
3. test_vault_list_files              -- list returns user's files
4. test_secure_put_endpoint           -- API endpoint works
5. test_secure_get_endpoint           -- API endpoint returns 403 (DRM block)
6. test_secure_put_creates_governed_file -- upload creates GovernedFile record
7. test_secure_put_creates_audit_log  -- upload creates FileAuditLog record
8. test_secure_get_not_found          -- 403 regardless of vault_id (DRM block)
"""

import base64
import io
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.file_audit import FileAuditLog
from app.models.file_governance import GovernedFile
from app.services.s3_vault import S3VaultService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_s3_client():
    """Return a MagicMock that stands in for boto3.client('s3', ...)."""
    return MagicMock()


@pytest.fixture()
def vault_service(mock_s3_client):
    """S3VaultService with boto3 patched out."""
    with patch("app.services.s3_vault.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_s3_client
        svc = S3VaultService(
            bucket_name="test-bucket",
            kms_key_id="test-kms-key",
            region="ap-northeast-2",
        )
        yield svc, mock_s3_client


# ---------------------------------------------------------------------------
# Unit tests — S3VaultService
# ---------------------------------------------------------------------------


def test_upload_to_vault(vault_service):
    """upload_file() calls put_object and returns vault_id, s3_key, expires_at."""
    svc, mock_s3 = vault_service
    mock_s3.put_object.return_value = {}

    result = svc.upload_file(
        username="TESTUSER01",
        filename="secret.csv",
        file_data=b"sensitive,data\n1,2",
        ttl_days=7,
    )

    assert "vault_id" in result
    assert "s3_key" in result
    assert "expires_at" in result

    vault_id = result["vault_id"]
    s3_key = result["s3_key"]

    # key follows vault/{username}/{vault_id}/{filename} pattern
    assert s3_key == f"vault/TESTUSER01/{vault_id}/secret.csv"
    assert len(vault_id) == 16  # first 16 hex chars of sha256

    # put_object was called once with encryption params
    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["Key"] == s3_key
    assert call_kwargs["ServerSideEncryption"] == "aws:kms"
    assert call_kwargs["SSEKMSKeyId"] == "test-kms-key"
    assert call_kwargs["Body"] == b"sensitive,data\n1,2"


def test_download_from_vault(vault_service):
    """download_file() returns (file_bytes, metadata) for a matching vault_id."""
    svc, mock_s3 = vault_service

    vault_id = "abcdef1234567890"
    s3_key = f"vault/TESTUSER01/{vault_id}/secret.csv"
    file_content = b"secret content here"

    mock_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": s3_key}]
    }
    mock_body = MagicMock()
    mock_body.read.return_value = file_content
    mock_s3.get_object.return_value = {
        "Body": mock_body,
        "Metadata": {
            "owner": "TESTUSER01",
            "vault-id": vault_id,
            "original-filename": "secret.csv",
            "expires-at": "2026-04-16T00:00:00+00:00",
        },
    }

    data, metadata = svc.download_file(username="TESTUSER01", vault_id=vault_id)

    assert data == file_content
    assert metadata["vault-id"] == vault_id
    assert metadata["original-filename"] == "secret.csv"

    mock_s3.list_objects_v2.assert_called_once_with(
        Bucket="test-bucket",
        Prefix=f"vault/TESTUSER01/{vault_id}/",
        MaxKeys=1,
    )
    mock_s3.get_object.assert_called_once_with(Bucket="test-bucket", Key=s3_key)


def test_download_from_vault_not_found(vault_service):
    """download_file() raises FileNotFoundError when vault_id does not exist."""
    svc, mock_s3 = vault_service
    mock_s3.list_objects_v2.return_value = {}  # no "Contents" key

    with pytest.raises(FileNotFoundError, match="not found"):
        svc.download_file(username="TESTUSER01", vault_id="nonexistent")


def test_vault_list_files(vault_service):
    """list_user_files() returns a list of file metadata dicts."""
    svc, mock_s3 = vault_service

    mock_s3.list_objects_v2.return_value = {
        "Contents": [
            {
                "Key": "vault/TESTUSER01/aaa111/secret.csv",
                "Size": 1024,
                "LastModified": datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc),
            },
            {
                "Key": "vault/TESTUSER01/bbb222/report.pdf",
                "Size": 2048,
                "LastModified": datetime(2026, 4, 8, 8, 0, 0, tzinfo=timezone.utc),
            },
        ]
    }

    files = svc.list_user_files(username="TESTUSER01")

    assert len(files) == 2
    assert files[0]["key"] == "vault/TESTUSER01/aaa111/secret.csv"
    assert files[0]["size"] == 1024
    assert "last_modified" in files[0]

    mock_s3.list_objects_v2.assert_called_once_with(
        Bucket="test-bucket",
        Prefix="vault/TESTUSER01/",
    )


def test_vault_list_files_empty(vault_service):
    """list_user_files() returns [] when there are no files."""
    svc, mock_s3 = vault_service
    mock_s3.list_objects_v2.return_value = {}  # no "Contents"

    files = svc.list_user_files(username="TESTUSER01")
    assert files == []


# ---------------------------------------------------------------------------
# Integration tests — secure_files router via TestClient
# ---------------------------------------------------------------------------

# We need a separate test app that includes the secure_files router.
# We patch S3VaultService at the module level to avoid real AWS calls.


def _make_vault_client(db_session):
    """Build a TestClient for the secure_files router with mocked S3 vault."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.core.config import get_settings, Settings
    from app.core.security import get_current_user_or_pod
    from app.routers.secure_files import router as secure_router

    test_app = FastAPI()
    test_app.include_router(secure_router)

    def _override_db():
        try:
            yield db_session
        finally:
            pass

    def _override_settings():
        return Settings(
            database_url="sqlite://",
            jwt_secret_key="test-secret",
            s3_vault_bucket="test-bucket",
            s3_vault_kms_key_id="test-kms-key",
        )

    def _override_user():
        return {"sub": "TESTUSER01", "role": "user", "name": "Test User"}

    test_app.dependency_overrides[get_db] = _override_db
    test_app.dependency_overrides[get_settings] = _override_settings
    test_app.dependency_overrides[get_current_user_or_pod] = _override_user

    return TestClient(test_app, raise_server_exceptions=False)


def _mock_vault_svc(upload_result=None, download_result=None, list_result=None):
    """Return a MagicMock S3VaultService."""
    mock_svc = MagicMock(spec=S3VaultService)
    if upload_result is not None:
        mock_svc.upload_file.return_value = upload_result
        # prepare_drm_upload is the new split-interface method used by secure_put.
        # It returns the same dict plus a plaintext_dek bytes field (kept in-memory only).
        mock_svc.prepare_drm_upload.return_value = {
            **upload_result,
            "plaintext_dek": b"\x00" * 32,
        }
        # finalize_drm_upload returns None — MagicMock default is fine.
    if download_result is not None:
        mock_svc.download_file.return_value = download_result
    if list_result is not None:
        mock_svc.list_user_files.return_value = list_result
    return mock_svc


def test_secure_put_endpoint(db_session):
    """POST /api/v1/secure/put returns vault_id and expires_at."""
    upload_result = {
        "vault_id": "abc123def456abcd",
        "s3_key": "vault/TESTUSER01/abc123def456abcd/secret.csv",
        "expires_at": "2026-04-16T00:00:00+00:00",
        "encrypted_dek": "dGVzdC1lbmNyeXB0ZWQtZGVr",
    }

    with patch(
        "app.routers.secure_files._get_vault_service",
        return_value=_mock_vault_svc(upload_result=upload_result),
    ):
        client = _make_vault_client(db_session)
        response = client.post(
            "/api/v1/secure/put",
            files={"file": ("secret.csv", b"confidential data", "text/csv")},
            data={"ttl_days": "7"},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["vault_id"] == "abc123def456abcd"
    assert "expires_at" in data


def test_secure_get_endpoint(db_session):
    """POST /api/v1/secure/get returns 403 — DRM policy blocks direct download."""
    client = _make_vault_client(db_session)
    response = client.post(
        "/api/v1/secure/get",
        json={"vault_id": "abc123def456abcd", "duration_minutes": 60},
    )

    assert response.status_code == 403, response.text
    detail = response.json().get("detail", "")
    assert "view" in detail.lower()


def test_secure_get_not_found(db_session):
    """POST /api/v1/secure/get returns 403 regardless of vault_id — DRM block."""
    client = _make_vault_client(db_session)
    response = client.post(
        "/api/v1/secure/get",
        json={"vault_id": "nonexistent000000"},
    )

    assert response.status_code == 403, response.text


def test_secure_put_creates_governed_file(db_session):
    """POST /api/v1/secure/put creates a GovernedFile record in the DB."""
    upload_result = {
        "vault_id": "aabbccdd11223344",
        "s3_key": "vault/TESTUSER01/aabbccdd11223344/private.pdf",
        "expires_at": "2026-04-16T00:00:00+00:00",
        "encrypted_dek": "dGVzdC1lbmNyeXB0ZWQtZGVr",
    }

    with patch(
        "app.routers.secure_files._get_vault_service",
        return_value=_mock_vault_svc(upload_result=upload_result),
    ):
        client = _make_vault_client(db_session)
        response = client.post(
            "/api/v1/secure/put",
            files={"file": ("private.pdf", b"%PDF-content", "application/pdf")},
            data={"ttl_days": "7"},
        )

    assert response.status_code == 200, response.text

    # GovernedFile should have been created
    governed = db_session.query(GovernedFile).filter(
        GovernedFile.username == "TESTUSER01",
        GovernedFile.filename == "private.pdf",
    ).first()
    assert governed is not None
    assert governed.classification == "sensitive"
    assert governed.status == "active"
    assert governed.ttl_days == 7


def test_secure_put_creates_audit_log(db_session):
    """POST /api/v1/secure/put creates a FileAuditLog record."""
    upload_result = {
        "vault_id": "1122334455667788",
        "s3_key": "vault/TESTUSER01/1122334455667788/data.csv",
        "expires_at": "2026-04-16T00:00:00+00:00",
        "encrypted_dek": "dGVzdC1lbmNyeXB0ZWQtZGVr",
    }

    with patch(
        "app.routers.secure_files._get_vault_service",
        return_value=_mock_vault_svc(upload_result=upload_result),
    ):
        client = _make_vault_client(db_session)
        client.post(
            "/api/v1/secure/put",
            files={"file": ("data.csv", b"col1,col2\n1,2", "text/csv")},
        )

    audit = db_session.query(FileAuditLog).filter(
        FileAuditLog.username == "TESTUSER01",
        FileAuditLog.action == "vault_upload",
    ).first()
    assert audit is not None
    assert audit.filename == "data.csv"
    assert "vault_id" in audit.detail


def test_secure_put_governs_file_before_s3(db_session):
    """GovernedFile with encrypted_dek is committed before S3 upload.

    If finalize_drm_upload raises (S3 failure), the DB row must already exist
    with encrypted_dek set — that is the commit-before-S3 ordering invariant.
    """
    upload_result = {
        "vault_id": "deadbeef00112233",
        "s3_key": "vault/TESTUSER01/deadbeef00112233/crash.txt",
        "expires_at": "2026-04-16T00:00:00+00:00",
        "encrypted_dek": "dGVzdC1lbmNyeXB0ZWQtZGVr",
    }
    mock_svc = _mock_vault_svc(upload_result=upload_result)
    mock_svc.finalize_drm_upload.side_effect = RuntimeError("simulated S3 failure")

    with patch(
        "app.routers.secure_files._get_vault_service",
        return_value=mock_svc,
    ):
        client = _make_vault_client(db_session)
        response = client.post(
            "/api/v1/secure/put",
            files={"file": ("crash.txt", b"data", "text/plain")},
            data={"ttl_days": "7"},
        )

    assert response.status_code == 500

    # Despite S3 failure, GovernedFile must be in DB with encrypted_dek committed.
    governed = db_session.query(GovernedFile).filter(
        GovernedFile.username == "TESTUSER01",
        GovernedFile.vault_id == "deadbeef00112233",
    ).first()
    assert governed is not None, "GovernedFile must be committed before S3 upload"
    assert governed.encrypted_dek == "dGVzdC1lbmNyeXB0ZWQtZGVr", "encrypted_dek must be persisted"
