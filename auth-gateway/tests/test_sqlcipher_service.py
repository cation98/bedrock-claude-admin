"""Tests for SQLCipher key management service.

Covers key generation, retrieval, expiry, revocation, and multiple-key ordering.
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.services.sqlcipher_service import (
    SQLCipherKey,
    generate_key,
    get_key_hash,
    revoke_key,
)


# --------------- Helpers ---------------

def _make_expired_key(username: str, db_name: str, db) -> str:
    """Generate a key then back-date its expiry to force expiration."""
    key = generate_key(username, db_name, ttl_days=1, db=db)
    # Back-date expires_at to yesterday
    record = (
        db.query(SQLCipherKey)
        .filter(
            SQLCipherKey.username == username,
            SQLCipherKey.db_name == db_name,
        )
        .order_by(SQLCipherKey.created_at.desc())
        .first()
    )
    record.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    return key


# --------------- Tests ---------------

class TestGenerateKey:
    def test_generate_key_returns_hex(self, db_session):
        """generate_key returns a 64-character lowercase hex string (256-bit)."""
        key = generate_key("USER01", "test.db", ttl_days=7, db=db_session)
        assert isinstance(key, str)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_generate_key_persists(self, db_session):
        """generate_key creates a SQLCipherKey record in the database."""
        generate_key("USER01", "test.db", ttl_days=7, db=db_session)
        record = (
            db_session.query(SQLCipherKey)
            .filter(SQLCipherKey.username == "USER01", SQLCipherKey.db_name == "test.db")
            .first()
        )
        assert record is not None
        assert record.username == "USER01"
        assert record.db_name == "test.db"
        assert record.revoked == 0
        assert len(record.key_hash) == 64  # SHA-256 hex

    def test_generate_key_hash_matches(self, db_session):
        """The stored key_hash is SHA-256 of the returned plain-text key."""
        key = generate_key("USER01", "test.db", ttl_days=7, db=db_session)
        expected_hash = hashlib.sha256(key.encode()).hexdigest()
        record = (
            db_session.query(SQLCipherKey)
            .filter(SQLCipherKey.username == "USER01", SQLCipherKey.db_name == "test.db")
            .first()
        )
        assert record.key_hash == expected_hash

    def test_generate_key_sets_expiry(self, db_session):
        """generate_key sets expires_at to approx now + ttl_days."""
        before = datetime.now(timezone.utc)
        generate_key("USER01", "test.db", ttl_days=7, db=db_session)
        after = datetime.now(timezone.utc)

        record = (
            db_session.query(SQLCipherKey)
            .filter(SQLCipherKey.username == "USER01")
            .first()
        )
        assert record.expires_at is not None
        # SQLite may return naive datetimes — normalise to UTC-aware for comparison.
        expires = record.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        expected_min = before + timedelta(days=7)
        expected_max = after + timedelta(days=7)
        assert expected_min <= expires <= expected_max

    def test_generate_key_no_expiry_when_ttl_zero(self, db_session):
        """ttl_days=0 results in expires_at=None (no expiration)."""
        generate_key("USER01", "test.db", ttl_days=0, db=db_session)
        record = (
            db_session.query(SQLCipherKey)
            .filter(SQLCipherKey.username == "USER01")
            .first()
        )
        assert record.expires_at is None


class TestGetKeyHash:
    def test_get_key_hash_active(self, db_session):
        """get_key_hash returns the hash for a non-expired, non-revoked key."""
        key = generate_key("USER01", "hr.db", ttl_days=7, db=db_session)
        expected_hash = hashlib.sha256(key.encode()).hexdigest()

        result = get_key_hash("USER01", "hr.db", db=db_session)
        assert result == expected_hash

    def test_get_key_hash_not_found(self, db_session):
        """get_key_hash returns None when no key exists for the user/db."""
        result = get_key_hash("NOUSER", "nonexistent.db", db=db_session)
        assert result is None

    def test_get_key_hash_expired(self, db_session):
        """get_key_hash returns None for an expired key and marks it revoked."""
        _make_expired_key("USER01", "hr.db", db=db_session)

        result = get_key_hash("USER01", "hr.db", db=db_session)
        assert result is None

        # Confirm the record was auto-revoked
        record = (
            db_session.query(SQLCipherKey)
            .filter(SQLCipherKey.username == "USER01", SQLCipherKey.db_name == "hr.db")
            .first()
        )
        assert record.revoked == 1

    def test_get_key_hash_revoked(self, db_session):
        """get_key_hash returns None for a manually revoked key."""
        generate_key("USER01", "hr.db", ttl_days=7, db=db_session)
        revoke_key("USER01", "hr.db", db=db_session)

        result = get_key_hash("USER01", "hr.db", db=db_session)
        assert result is None


class TestRevokeKey:
    def test_revoke_key(self, db_session):
        """revoke_key marks the active key as revoked and returns True."""
        generate_key("USER01", "hr.db", ttl_days=7, db=db_session)

        result = revoke_key("USER01", "hr.db", db=db_session)
        assert result is True

        # Key should no longer be accessible
        assert get_key_hash("USER01", "hr.db", db=db_session) is None

    def test_revoke_key_no_active_key(self, db_session):
        """revoke_key returns False when no active key exists."""
        result = revoke_key("NOUSER", "none.db", db=db_session)
        assert result is False

    def test_revoke_key_only_affects_target(self, db_session):
        """revoke_key for one db_name does not affect other db_name keys."""
        generate_key("USER01", "hr.db", ttl_days=7, db=db_session)
        generate_key("USER01", "finance.db", ttl_days=7, db=db_session)

        revoke_key("USER01", "hr.db", db=db_session)

        # hr.db key is gone, finance.db key should still be accessible
        assert get_key_hash("USER01", "hr.db", db=db_session) is None
        assert get_key_hash("USER01", "finance.db", db=db_session) is not None


class TestMultipleKeys:
    def test_multiple_keys_returns_latest(self, db_session):
        """When multiple active keys exist for same user/db, the most recently created is returned."""
        key1 = generate_key("USER01", "hr.db", ttl_days=7, db=db_session)
        key2 = generate_key("USER01", "hr.db", ttl_days=7, db=db_session)

        hash1 = hashlib.sha256(key1.encode()).hexdigest()
        hash2 = hashlib.sha256(key2.encode()).hexdigest()

        result = get_key_hash("USER01", "hr.db", db=db_session)

        # Should return the latest key (key2)
        assert result == hash2
        assert result != hash1

    def test_multiple_keys_revoke_all(self, db_session):
        """revoke_key revokes all active keys for the given user/db."""
        generate_key("USER01", "hr.db", ttl_days=7, db=db_session)
        generate_key("USER01", "hr.db", ttl_days=7, db=db_session)

        result = revoke_key("USER01", "hr.db", db=db_session)
        assert result is True

        # Confirm both records are revoked
        active_count = (
            db_session.query(SQLCipherKey)
            .filter(
                SQLCipherKey.username == "USER01",
                SQLCipherKey.db_name == "hr.db",
                SQLCipherKey.revoked == 0,
            )
            .count()
        )
        assert active_count == 0
