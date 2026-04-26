#!/usr/bin/env python3
"""DRM Phase 2 backfill — encrypt PLAIN vault files in-place with AES-256-GCM.

FSM: PLAIN → ENCRYPTING (claimed) → ENCRYPTED (success) | leave ENCRYPTING (failure)
Stale ENCRYPTING rows (> STALE_THRESHOLD_MINUTES) are reset to PLAIN by --reset-stale.

Usage (run from auth-gateway/ with venv active):
    python ../scripts/drm_backfill.py --dry-run
    python ../scripts/drm_backfill.py --apply
    python ../scripts/drm_backfill.py --apply --batch-size 50
    python ../scripts/drm_backfill.py --reset-stale

Required env vars:
    DATABASE_URL           postgresql://... platform DB
    S3_VAULT_BUCKET        Vault S3 bucket name
    S3_VAULT_KMS_KEY_ID    KMS key ARN or alias
    S3_VAULT_REGION        AWS region (default: ap-northeast-2)
"""

import argparse
import logging
import os
import struct
import sys
from base64 import b64encode

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Inlined crypto helpers (mirrors app/core/dek_utils.py — no app imports)
# ---------------------------------------------------------------------------

_MAGIC = b"DRM1"
_NONCE_LEN = 12
_DEK_LEN = 32


def _aad(vault_id: str, s3_key: str) -> bytes:
    v, k = vault_id.encode(), s3_key.encode()
    return struct.pack(">I", len(v)) + v + struct.pack(">I", len(k)) + k


def is_drm_encrypted(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == _MAGIC


def _encrypt_file(plaintext: bytes, dek: bytes, vault_id: str, s3_key: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(dek).encrypt(nonce, plaintext, _aad(vault_id, s3_key))
    return _MAGIC + nonce + ct


def _kms_encrypt_dek(kms_client, kms_key_id: str, vault_id: str, s3_key: str):
    """Returns (plaintext_dek: bytes, encrypted_dek_b64: str)."""
    plaintext_dek = os.urandom(_DEK_LEN)
    resp = kms_client.encrypt(
        KeyId=kms_key_id,
        Plaintext=plaintext_dek,
        EncryptionContext={"vault_id": vault_id, "s3_key": s3_key},
    )
    return plaintext_dek, b64encode(resp["CiphertextBlob"]).decode()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STALE_THRESHOLD_MINUTES = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("drm_backfill")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _claim_batch(conn, batch_size: int) -> list:
    """SELECT PLAIN rows FOR UPDATE SKIP LOCKED, mark ENCRYPTING, return rows."""
    rows = conn.execute(
        text(
            "SELECT id, username, vault_id, file_path, filename "
            "FROM governed_files "
            "WHERE encryption_state = 'plain' AND vault_id IS NOT NULL "
            "LIMIT :n "
            "FOR UPDATE SKIP LOCKED"
        ).bindparams(n=batch_size)
    ).fetchall()

    if not rows:
        return []

    id_placeholders = ", ".join(f":id{i}" for i in range(len(rows)))
    params = {f"id{i}": rows[i].id for i in range(len(rows))}
    conn.execute(
        text(
            f"UPDATE governed_files "
            f"SET encryption_state = 'encrypting', updated_at = NOW() "
            f"WHERE id IN ({id_placeholders})"
        ).bindparams(**params)
    )
    return rows


def _save_dek_before_s3(engine, file_id: int, encrypted_dek_b64: str) -> None:
    """Persist encrypted DEK to DB while state stays 'encrypting'.

    Called BEFORE s3.put_object to eliminate the crash window where S3 holds
    ciphertext but the DEK is only in memory. If the process dies after this
    and before put_object, S3 still has plaintext — is_drm_encrypted() → False
    → plain download path works. If the process dies after put_object but before
    _mark_encrypted, the DEK is in DB and download_file() decrypts correctly.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE governed_files "
                "SET encrypted_dek = :dek, "
                "    updated_at = NOW() "
                "WHERE id = :id"
            ).bindparams(id=file_id, dek=encrypted_dek_b64)
        )


def _mark_encrypted(engine, file_id: int) -> None:
    """Set encryption_state=encrypted after S3 overwrite succeeds."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE governed_files "
                "SET encryption_state = 'encrypted', "
                "    backfill_completed_at = NOW(), "
                "    updated_at = NOW() "
                "WHERE id = :id"
            ).bindparams(id=file_id)
        )



# ---------------------------------------------------------------------------
# S3 encrypt-in-place
# ---------------------------------------------------------------------------

def _encrypt_row(s3, kms, engine, bucket: str, kms_key_id: str, row) -> bool:
    """Download plain S3 object, re-encrypt with AES-256-GCM, overwrite in-place.

    Crash-safe ordering:
      1. Generate DEK via KMS
      2. Persist encrypted_dek to DB (state stays 'encrypting')  ← point of no return
      3. Put ciphertext to S3
    If crash between steps 1-2: S3 still plain → plain-path download works.
    If crash between steps 2-3: S3 still plain, DEK in DB → still plain-path works.
    If crash between steps 3-?: S3 has ciphertext, DEK in DB → download_file decrypts.

    Returns True on success, False on any error.
    """
    vault_id: str = row.vault_id
    s3_key: str = row.file_path

    # Download current S3 object
    try:
        obj = s3.get_object(Bucket=bucket, Key=s3_key)
        raw: bytes = obj["Body"].read()
        existing_meta: dict = obj.get("Metadata", {})
    except ClientError as exc:
        logger.error("S3 get_object failed id=%d key=%s: %s", row.id, s3_key, exc)
        return False

    # Guard: already DRM-encrypted (data inconsistency — state mismatch)
    if is_drm_encrypted(raw):
        logger.warning(
            "id=%d already DRM-encrypted but state=PLAIN — skipping for manual review",
            row.id,
        )
        return False

    # Generate DEK via KMS
    try:
        plaintext_dek, encrypted_dek_b64 = _kms_encrypt_dek(kms, kms_key_id, vault_id, s3_key)
    except Exception as exc:
        logger.error("KMS encrypt_dek failed id=%d vault_id=%s: %s", row.id, vault_id, exc)
        return False

    # Persist DEK to DB BEFORE S3 overwrite (eliminates permanent-inaccessibility window)
    try:
        _save_dek_before_s3(engine, row.id, encrypted_dek_b64)
    except Exception as exc:
        logger.error("DB DEK write failed id=%d: %s — aborting S3 overwrite", row.id, exc)
        return False

    # Encrypt plaintext
    try:
        ciphertext = _encrypt_file(raw, plaintext_dek, vault_id, s3_key)
    except Exception as exc:
        logger.error("AES encrypt failed id=%d vault_id=%s: %s", row.id, vault_id, exc)
        return False

    # Overwrite S3 object with ciphertext
    upload_meta = {k: v for k, v in existing_meta.items() if k != "drm-version"}
    upload_meta["drm-version"] = "1"
    try:
        s3.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=ciphertext,
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=kms_key_id,
            Metadata=upload_meta,
        )
    except ClientError as exc:
        logger.error("S3 put_object failed id=%d key=%s: %s", row.id, s3_key, exc)
        # DEK is in DB; S3 still has plaintext. download_file plain-path still works.
        return False

    logger.info(
        "Encrypted id=%d user=%s vault_id=%s size=%d→%d",
        row.id, row.username, vault_id, len(raw), len(ciphertext),
    )
    return True


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_dry_run(engine, batch_size: int) -> None:
    sample_limit = min(batch_size, 10)
    with engine.connect() as conn:
        total = conn.execute(
            text(
                "SELECT COUNT(*) FROM governed_files "
                "WHERE encryption_state = 'plain' AND vault_id IS NOT NULL"
            )
        ).scalar()
        stale = conn.execute(
            text(
                f"SELECT COUNT(*) FROM governed_files "
                f"WHERE encryption_state = 'encrypting' "
                f"  AND updated_at < NOW() - INTERVAL '{STALE_THRESHOLD_MINUTES} minutes'"
            )
        ).scalar()
        sample = conn.execute(
            text(
                "SELECT id, username, vault_id, file_path "
                "FROM governed_files "
                "WHERE encryption_state = 'plain' AND vault_id IS NOT NULL "
                "ORDER BY id "
                "LIMIT :lim"
            ).bindparams(lim=sample_limit)
        ).fetchall()

    print()
    print("=== DRM Backfill Dry-Run ===")
    print(f"PLAIN rows to backfill   : {total}")
    print(f"Stale ENCRYPTING rows    : {stale}  (stuck > {STALE_THRESHOLD_MINUTES}m)")
    if sample:
        print(f"\nFirst {len(sample)} sample rows:")
        for r in sample:
            print(f"  id={r.id:6d}  user={r.username:<20}  vault_id={r.vault_id}  key={r.file_path}")
    else:
        print("\nNo PLAIN rows found — nothing to backfill.")
    print()
    print("Run with --apply to execute.")
    print()


def cmd_apply(engine, s3, kms, bucket: str, kms_key_id: str, batch_size: int) -> None:
    total_encrypted = 0
    total_left_encrypting = 0
    batch_num = 0

    while True:
        batch_num += 1
        with engine.begin() as conn:
            rows = _claim_batch(conn, batch_size)

        if not rows:
            logger.info("No more PLAIN rows. Done.")
            break

        logger.info("Batch %d: claimed %d rows for encryption", batch_num, len(rows))

        for row in rows:
            ok = _encrypt_row(s3, kms, engine, bucket, kms_key_id, row)
            if ok:
                _mark_encrypted(engine, row.id)
                total_encrypted += 1
            else:
                logger.warning("id=%d left in ENCRYPTING state — run --reset-stale to retry", row.id)
                total_left_encrypting += 1

    print()
    print("=== DRM Backfill Complete ===")
    print(f"Encrypted          : {total_encrypted}")
    print(f"Left in ENCRYPTING : {total_left_encrypting}  (run --reset-stale to retry)")
    print()


def cmd_reset_stale(engine) -> None:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                f"UPDATE governed_files "
                f"SET encryption_state = 'plain', updated_at = NOW() "
                f"WHERE encryption_state = 'encrypting' "
                f"  AND updated_at < NOW() - INTERVAL '{STALE_THRESHOLD_MINUTES} minutes' "
                f"RETURNING id, username, vault_id"
            )
        )
        rows = result.fetchall()

    logger.info("Reset %d stale ENCRYPTING rows → PLAIN", len(rows))
    for r in rows:
        logger.info("  reset: id=%d user=%s vault_id=%s", r.id, r.username, r.vault_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="DRM Phase 2 backfill script")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Write to S3 and DB (default: dry-run preview)")
    mode.add_argument("--reset-stale", action="store_true", help="Reset stuck ENCRYPTING rows to PLAIN")
    parser.add_argument("--batch-size", type=int, default=20, help="Rows per batch (default: 20)")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        logger.error("DATABASE_URL is not set")
        sys.exit(1)

    engine = create_engine(database_url, pool_pre_ping=True)

    if args.reset_stale:
        cmd_reset_stale(engine)
    elif args.apply:
        bucket = os.environ.get("S3_VAULT_BUCKET", "")
        kms_key_id = os.environ.get("S3_VAULT_KMS_KEY_ID", "")
        region = os.environ.get("S3_VAULT_REGION", "ap-northeast-2")
        if not bucket or not kms_key_id:
            logger.error("S3_VAULT_BUCKET and S3_VAULT_KMS_KEY_ID must be set for --apply")
            sys.exit(1)
        s3 = boto3.client("s3", region_name=region)
        kms = boto3.client("kms", region_name=region)
        cmd_apply(engine, s3, kms, bucket, kms_key_id, args.batch_size)
    else:
        cmd_dry_run(engine, args.batch_size)


if __name__ == "__main__":
    main()
