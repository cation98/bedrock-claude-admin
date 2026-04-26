"""Tests for scripts/drm_backfill.py — _encrypt_row ordering invariant.

Covered:
1. test_encrypt_row_happy_path        -- returns True; _save_dek_before_s3 called BEFORE s3.put_object
2. test_encrypt_row_s3_failure        -- S3 put_object fails; _save_dek_before_s3 already called; returns False
3. test_encrypt_row_already_encrypted -- body has DRM1 magic; _save_dek_before_s3 NOT called; returns False
4. test_encrypt_row_kms_failure       -- kms.encrypt raises; _save_dek_before_s3 NOT called; returns False

NOTE: _claim_batch, _mark_encrypted, cmd_apply use PostgreSQL-only syntax
(FOR UPDATE SKIP LOCKED, INTERVAL literals) and require a live PostgreSQL connection.
Those functions are not covered here — add integration tests when a PostgreSQL fixture
is available.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

# drm_backfill.py lives outside the auth-gateway package tree — add scripts/ to path.
_SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from drm_backfill import _encrypt_row  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_VAULT_ID = "aabbccdd11223344"
_S3_KEY = f"vault/TESTUSER01/{_VAULT_ID}/secret.csv"
_PLAIN_BODY = b"plain file content - not DRM-encrypted"


def _make_row(**overrides):
    defaults = dict(id=42, vault_id=_VAULT_ID, file_path=_S3_KEY, username="TESTUSER01")
    return SimpleNamespace(**{**defaults, **overrides})


def _make_s3(body: bytes = _PLAIN_BODY) -> MagicMock:
    mock_body = MagicMock()
    mock_body.read.return_value = body
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": mock_body, "Metadata": {}}
    s3.put_object.return_value = {}
    return s3


def _make_kms() -> MagicMock:
    kms = MagicMock()
    kms.encrypt.return_value = {"CiphertextBlob": b"\xde\xad" + b"\x00" * 32}
    return kms


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_encrypt_row_happy_path():
    """_encrypt_row returns True and _save_dek_before_s3 is called BEFORE s3.put_object."""
    s3 = _make_s3()
    kms = _make_kms()
    engine = MagicMock()
    row = _make_row()

    call_order: list[str] = []

    def _record_save(eng, file_id, encrypted_dek_b64):
        call_order.append("save_dek")

    def _record_put(**kwargs):
        call_order.append("s3_put")
        return {}

    s3.put_object.side_effect = _record_put

    with patch("drm_backfill._save_dek_before_s3", side_effect=_record_save):
        result = _encrypt_row(s3, kms, engine, "test-bucket", "alias/test-key", row)

    assert result is True
    assert "save_dek" in call_order, "_save_dek_before_s3 must be called"
    assert "s3_put" in call_order, "s3.put_object must be called"
    assert call_order.index("save_dek") < call_order.index("s3_put"), (
        "_save_dek_before_s3 must be called before s3.put_object — "
        "DEK must be in DB before ciphertext lands in S3"
    )


def test_encrypt_row_s3_failure():
    """S3 put_object failure: _save_dek_before_s3 already called before the error; returns False.

    This verifies the crash-safe invariant: DEK is persisted even when S3 upload fails,
    so the file is recoverable once S3 is healthy again.
    """
    s3 = _make_s3()
    kms = _make_kms()
    engine = MagicMock()
    row = _make_row()

    save_called: list[bool] = []

    def _record_save(eng, file_id, encrypted_dek_b64):
        save_called.append(True)

    s3.put_object.side_effect = ClientError(
        {"Error": {"Code": "ServiceUnavailable", "Message": "S3 unavailable"}},
        "PutObject",
    )

    with patch("drm_backfill._save_dek_before_s3", side_effect=_record_save):
        result = _encrypt_row(s3, kms, engine, "test-bucket", "alias/test-key", row)

    assert result is False
    assert save_called, (
        "_save_dek_before_s3 must have been called before the S3 failure — "
        "DEK must survive the crash window"
    )


def test_encrypt_row_already_encrypted():
    """Body already starts with DRM1 magic: _save_dek_before_s3 NOT called; returns False.

    State mismatch (S3 already ciphertext but DB says PLAIN) is flagged for manual review
    without touching DB or re-encrypting.
    """
    drm_body = b"DRM1" + b"\x00" * 100
    s3 = _make_s3(body=drm_body)
    kms = _make_kms()
    engine = MagicMock()
    row = _make_row()

    with patch("drm_backfill._save_dek_before_s3") as mock_save:
        result = _encrypt_row(s3, kms, engine, "test-bucket", "alias/test-key", row)

    assert result is False
    mock_save.assert_not_called()


def test_encrypt_row_kms_failure():
    """KMS failure before DEK write: _save_dek_before_s3 NOT called; returns False.

    No DB writes occur when KMS is unreachable — S3 still holds plaintext and the
    plain-path download continues to work.
    """
    s3 = _make_s3()
    kms = MagicMock()
    kms.encrypt.side_effect = Exception("KMS service error")
    engine = MagicMock()
    row = _make_row()

    with patch("drm_backfill._save_dek_before_s3") as mock_save:
        result = _encrypt_row(s3, kms, engine, "test-bucket", "alias/test-key", row)

    assert result is False
    mock_save.assert_not_called()
