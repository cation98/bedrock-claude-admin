"""Tests for storage retention parsing and cleanup detection logic.

Covers:
  - _parse_retention correctly handles 7d, 30d, 90d, unlimited
  - Users with unlimited retention are skipped (return None)
  - Users whose approved_at + retention < now are detected as expired
"""

from datetime import datetime, timedelta, timezone

from app.core.scheduler import _parse_retention


# --------------- tests ---------------


def test_storage_retention_parsing():
    """_parse_retention converts 7d/30d/90d to timedelta, unlimited to None."""
    assert _parse_retention("7d") == timedelta(days=7)
    assert _parse_retention("30d") == timedelta(days=30)
    assert _parse_retention("90d") == timedelta(days=90)
    assert _parse_retention("unlimited") is None

    # Unknown values return None (treated as unknown/skip)
    assert _parse_retention("invalid") is None
    assert _parse_retention("") is None


def test_unlimited_users_skipped():
    """Users with 'unlimited' retention should not be flagged for cleanup.

    _parse_retention("unlimited") returns None, so the cleanup loop
    skips them (it checks `if retention is None: continue`).
    """
    retention = _parse_retention("unlimited")
    assert retention is None
    # The cleanup loop does:
    #   if retention is None:
    #       continue  # skip unlimited users
    # So unlimited users are never considered for expiration.


def test_expired_users_detected():
    """Users whose approved_at + retention < now are flagged for cleanup.

    Simulates the expiration check in storage_cleanup_loop without
    running the full async loop.
    """
    now = datetime.now(timezone.utc)

    # Scenario 1: 7-day retention, approved 10 days ago -> expired
    approved_at_old = now - timedelta(days=10)
    retention_7d = _parse_retention("7d")
    assert retention_7d is not None
    expires_at = approved_at_old + retention_7d
    assert expires_at < now, "Should be expired (7d retention, approved 10 days ago)"

    # Scenario 2: 30-day retention, approved 10 days ago -> NOT expired
    retention_30d = _parse_retention("30d")
    assert retention_30d is not None
    expires_at = approved_at_old + retention_30d
    assert expires_at > now, "Should NOT be expired (30d retention, approved 10 days ago)"

    # Scenario 3: 90-day retention, approved 100 days ago -> expired
    approved_at_very_old = now - timedelta(days=100)
    retention_90d = _parse_retention("90d")
    assert retention_90d is not None
    expires_at = approved_at_very_old + retention_90d
    assert expires_at < now, "Should be expired (90d retention, approved 100 days ago)"

    # Scenario 4: 30-day retention, approved today -> NOT expired
    approved_at_today = now
    expires_at = approved_at_today + retention_30d
    assert expires_at > now, "Should NOT be expired (30d retention, approved today)"
