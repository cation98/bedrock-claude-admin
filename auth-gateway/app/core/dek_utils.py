"""AES-256-GCM envelope encryption helpers for DRM Phase 2.

Binary format on S3:
  [magic: 4B "DRM1"][nonce: 12B][ciphertext + GCM_tag: N+16 bytes]

AAD construction uses length-prefixed fields:
  struct.pack(">I", len(v)) + v + struct.pack(">I", len(k)) + k
  Avoids separator-injection when vault_id/s3_key contain delimiter characters.

KMS EncryptionContext mirrors AAD binding:
  {"vault_id": vault_id, "s3_key": s3_key}
  If the S3 key changes (rename/copy), KMS.Decrypt will also fail — by design,
  ensuring ciphertext and key metadata remain bound. Future object migrations
  require re-encryption.
"""

import os
import struct
from base64 import b64decode, b64encode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_MAGIC = b"DRM1"
_NONCE_LEN = 12
_DEK_LEN = 32  # AES-256


def _aad(vault_id: str, s3_key: str) -> bytes:
    """Build Additional Authenticated Data with length-prefixed fields."""
    v = vault_id.encode()
    k = s3_key.encode()
    return struct.pack(">I", len(v)) + v + struct.pack(">I", len(k)) + k


def is_drm_encrypted(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == _MAGIC


def encrypt_file(plaintext: bytes, dek: bytes, vault_id: str, s3_key: str) -> bytes:
    """AES-256-GCM encrypt. Returns magic(4) + nonce(12) + ciphertext+tag."""
    nonce = os.urandom(_NONCE_LEN)
    aad = _aad(vault_id, s3_key)
    ct_with_tag = AESGCM(dek).encrypt(nonce, plaintext, aad)
    return _MAGIC + nonce + ct_with_tag


def decrypt_file(data: bytes, dek: bytes, vault_id: str, s3_key: str) -> bytes:
    """AES-256-GCM decrypt. data must start with magic(4) + nonce(12)."""
    if data[:4] != _MAGIC:
        raise ValueError("Not a DRM-encrypted file (missing DRM1 magic)")
    nonce = data[4:16]
    ct_with_tag = data[16:]
    aad = _aad(vault_id, s3_key)
    return AESGCM(dek).decrypt(nonce, ct_with_tag, aad)


def kms_encrypt_dek(
    kms_client,
    kms_key_id: str,
    vault_id: str,
    s3_key: str,
) -> tuple[bytes, str]:
    """Generate random 32-byte DEK and KMS-encrypt it.

    Returns (plaintext_dek, encrypted_dek_b64).
    EncryptionContext binds DEK to file identity (mirrors AAD).
    """
    plaintext_dek = os.urandom(_DEK_LEN)
    response = kms_client.encrypt(
        KeyId=kms_key_id,
        Plaintext=plaintext_dek,
        EncryptionContext={"vault_id": vault_id, "s3_key": s3_key},
    )
    return plaintext_dek, b64encode(response["CiphertextBlob"]).decode()


def kms_decrypt_dek(
    kms_client,
    encrypted_dek_b64: str,
    vault_id: str,
    s3_key: str,
) -> bytes:
    """Decrypt KMS-encrypted DEK. Returns 32-byte plaintext DEK."""
    response = kms_client.decrypt(
        CiphertextBlob=b64decode(encrypted_dek_b64),
        EncryptionContext={"vault_id": vault_id, "s3_key": s3_key},
    )
    return response["Plaintext"]
