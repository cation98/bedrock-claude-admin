"""Unit tests for app/core/dek_utils.py — AES-256-GCM envelope encryption helpers.

Covers:
1. test_encrypt_decrypt_roundtrip      -- encrypt then decrypt returns original plaintext
2. test_is_drm_encrypted_true          -- DRM1 magic header detected
3. test_is_drm_encrypted_false         -- plain bytes not flagged as DRM
4. test_decrypt_wrong_dek_raises       -- wrong DEK → InvalidTag
5. test_decrypt_wrong_vault_id_raises  -- wrong vault_id in AAD → InvalidTag
6. test_decrypt_wrong_s3_key_raises    -- wrong s3_key in AAD → InvalidTag
7. test_decrypt_truncated_raises       -- truncated ciphertext → error
8. test_kms_encrypt_dek_calls          -- kms.encrypt called with correct EncryptionContext
9. test_kms_decrypt_dek_calls          -- kms.decrypt called with correct CiphertextBlob
10. test_kms_roundtrip                 -- encrypt then decrypt DEK via mocked KMS
"""

import os
from base64 import b64decode, b64encode
from unittest.mock import MagicMock

import pytest
from cryptography.exceptions import InvalidTag

from app.core.dek_utils import (
    _aad,
    decrypt_file,
    encrypt_file,
    is_drm_encrypted,
    kms_decrypt_dek,
    kms_encrypt_dek,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VAULT_ID = "abcdef1234567890"
_S3_KEY = f"vault/TESTUSER01/{_VAULT_ID}/secret.csv"
_PLAINTEXT = b"sensitive data \x00\x01\x02 end"
_DEK = os.urandom(32)  # module-level so encrypt/decrypt tests share same DEK


# ---------------------------------------------------------------------------
# AAD + magic
# ---------------------------------------------------------------------------


def test_is_drm_encrypted_true():
    ciphertext = encrypt_file(_PLAINTEXT, _DEK, _VAULT_ID, _S3_KEY)
    assert is_drm_encrypted(ciphertext) is True


def test_is_drm_encrypted_false_plain():
    assert is_drm_encrypted(b"hello world") is False


def test_is_drm_encrypted_false_empty():
    assert is_drm_encrypted(b"") is False


def test_is_drm_encrypted_false_short():
    assert is_drm_encrypted(b"DRM") is False  # 3 bytes, not 4


def test_aad_length_prefix_no_separator_injection():
    """AAD is length-prefixed: vault_id with embedded '/' doesn't collide."""
    aad_a = _aad("abc", "def/ghi")
    aad_b = _aad("abc/def", "ghi")
    assert aad_a != aad_b


# ---------------------------------------------------------------------------
# Encrypt / decrypt roundtrip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip():
    dek = os.urandom(32)
    ciphertext = encrypt_file(_PLAINTEXT, dek, _VAULT_ID, _S3_KEY)
    recovered = decrypt_file(ciphertext, dek, _VAULT_ID, _S3_KEY)
    assert recovered == _PLAINTEXT


def test_encrypt_produces_drm_magic():
    dek = os.urandom(32)
    ciphertext = encrypt_file(b"data", dek, _VAULT_ID, _S3_KEY)
    assert ciphertext[:4] == b"DRM1"


def test_encrypt_nonce_is_random():
    """Two encryptions of same plaintext produce different ciphertexts (different nonces)."""
    dek = os.urandom(32)
    c1 = encrypt_file(_PLAINTEXT, dek, _VAULT_ID, _S3_KEY)
    c2 = encrypt_file(_PLAINTEXT, dek, _VAULT_ID, _S3_KEY)
    assert c1 != c2  # different random nonces


def test_encrypt_empty_plaintext():
    dek = os.urandom(32)
    ciphertext = encrypt_file(b"", dek, _VAULT_ID, _S3_KEY)
    recovered = decrypt_file(ciphertext, dek, _VAULT_ID, _S3_KEY)
    assert recovered == b""


# ---------------------------------------------------------------------------
# Integrity violations → InvalidTag
# ---------------------------------------------------------------------------


def test_decrypt_wrong_dek_raises():
    dek = os.urandom(32)
    wrong_dek = os.urandom(32)
    ciphertext = encrypt_file(_PLAINTEXT, dek, _VAULT_ID, _S3_KEY)
    with pytest.raises(InvalidTag):
        decrypt_file(ciphertext, wrong_dek, _VAULT_ID, _S3_KEY)


def test_decrypt_wrong_vault_id_raises():
    dek = os.urandom(32)
    ciphertext = encrypt_file(_PLAINTEXT, dek, _VAULT_ID, _S3_KEY)
    with pytest.raises(InvalidTag):
        decrypt_file(ciphertext, dek, "different_vault_id_00", _S3_KEY)


def test_decrypt_wrong_s3_key_raises():
    dek = os.urandom(32)
    ciphertext = encrypt_file(_PLAINTEXT, dek, _VAULT_ID, _S3_KEY)
    with pytest.raises(InvalidTag):
        decrypt_file(ciphertext, dek, _VAULT_ID, "vault/OTHER/tampered/key.csv")


def test_decrypt_bit_flip_raises():
    """Single-bit flip in ciphertext body is detected by GCM tag."""
    dek = os.urandom(32)
    ciphertext = encrypt_file(_PLAINTEXT, dek, _VAULT_ID, _S3_KEY)
    tampered = bytearray(ciphertext)
    tampered[20] ^= 0xFF  # flip byte in ciphertext body (after magic+nonce)
    with pytest.raises(InvalidTag):
        decrypt_file(bytes(tampered), dek, _VAULT_ID, _S3_KEY)


def test_decrypt_missing_magic_raises():
    dek = os.urandom(32)
    with pytest.raises(ValueError, match="DRM1"):
        decrypt_file(b"XXXX" + b"\x00" * 28, dek, _VAULT_ID, _S3_KEY)


def test_decrypt_truncated_raises():
    """Ciphertext truncated to just magic+nonce raises (no tag)."""
    dek = os.urandom(32)
    ciphertext = encrypt_file(_PLAINTEXT, dek, _VAULT_ID, _S3_KEY)
    # Keep only magic(4) + nonce(12) = 16 bytes — no ciphertext or tag
    with pytest.raises(Exception):
        decrypt_file(ciphertext[:16], dek, _VAULT_ID, _S3_KEY)


# ---------------------------------------------------------------------------
# KMS helpers (mocked)
# ---------------------------------------------------------------------------


def _make_kms_mock(plaintext_dek: bytes) -> MagicMock:
    """Return a mock KMS client that mirrors real Encrypt/Decrypt behaviour."""
    mock_kms = MagicMock()
    fake_blob = b"\xde\xad" + plaintext_dek  # fake CiphertextBlob
    mock_kms.encrypt.return_value = {"CiphertextBlob": fake_blob}
    mock_kms.decrypt.return_value = {"Plaintext": plaintext_dek}
    return mock_kms


def test_kms_encrypt_dek_calls_encrypt():
    plaintext_dek = os.urandom(32)
    mock_kms = _make_kms_mock(plaintext_dek)

    returned_plaintext, encrypted_b64 = kms_encrypt_dek(
        mock_kms, "alias/test-key", _VAULT_ID, _S3_KEY
    )

    mock_kms.encrypt.assert_called_once()
    call_kwargs = mock_kms.encrypt.call_args.kwargs
    assert call_kwargs["KeyId"] == "alias/test-key"
    assert call_kwargs["EncryptionContext"] == {
        "vault_id": _VAULT_ID,
        "s3_key": _S3_KEY,
    }
    assert isinstance(returned_plaintext, bytes)
    assert len(returned_plaintext) == 32
    assert isinstance(encrypted_b64, str)
    # encrypted_b64 is base64 of the fake CiphertextBlob
    assert b64decode(encrypted_b64) == mock_kms.encrypt.return_value["CiphertextBlob"]


def test_kms_decrypt_dek_calls_decrypt():
    plaintext_dek = os.urandom(32)
    mock_kms = _make_kms_mock(plaintext_dek)
    fake_blob = b"\xde\xad" + plaintext_dek
    encrypted_b64 = b64encode(fake_blob).decode()

    result = kms_decrypt_dek(mock_kms, encrypted_b64, _VAULT_ID, _S3_KEY)

    mock_kms.decrypt.assert_called_once()
    call_kwargs = mock_kms.decrypt.call_args.kwargs
    assert call_kwargs["CiphertextBlob"] == fake_blob
    assert call_kwargs["EncryptionContext"] == {
        "vault_id": _VAULT_ID,
        "s3_key": _S3_KEY,
    }
    assert result == plaintext_dek


def test_kms_encrypt_decrypt_roundtrip():
    """Full envelope roundtrip via mocked KMS.

    kms_encrypt_dek generates a random DEK internally. We capture it via a
    side_effect on mock.encrypt, then return it from mock.decrypt — simulating
    what real KMS would do (encrypt DEK blob → later decrypt → same DEK bytes).
    """
    captured: list[bytes] = []
    fake_blob = b"\xca\xfe\xba\xbe" * 4  # fake CiphertextBlob (16 bytes)

    def _mock_encrypt(**kwargs):
        captured.append(kwargs["Plaintext"])
        return {"CiphertextBlob": fake_blob}

    def _mock_decrypt(**kwargs):
        return {"Plaintext": captured[0]}

    mock_kms = MagicMock()
    mock_kms.encrypt.side_effect = _mock_encrypt
    mock_kms.decrypt.side_effect = _mock_decrypt

    returned_dek, encrypted_b64 = kms_encrypt_dek(
        mock_kms, "alias/test-key", _VAULT_ID, _S3_KEY
    )
    assert returned_dek == captured[0]  # same DEK that was generated internally

    # Encrypt file with the DEK returned from kms_encrypt_dek
    ciphertext = encrypt_file(_PLAINTEXT, returned_dek, _VAULT_ID, _S3_KEY)

    # Decrypt DEK via mocked KMS (simulates real KMS decrypt returning same bytes)
    recovered_dek = kms_decrypt_dek(mock_kms, encrypted_b64, _VAULT_ID, _S3_KEY)
    assert recovered_dek == returned_dek

    # Decrypt file with recovered DEK — full end-to-end roundtrip
    recovered_plaintext = decrypt_file(ciphertext, recovered_dek, _VAULT_ID, _S3_KEY)
    assert recovered_plaintext == _PLAINTEXT
