"""Phase 1a: ops/export/_common.py 유틸 테스트."""
from ops.export._common import mask_pii


def test_mask_pii_email():
    assert mask_pii("user@skons.net") == "u***@skons.net"


def test_mask_pii_email_short_local():
    """단일문자 local part: u@... → u***@skons.net."""
    assert mask_pii("u@skons.net") == "u***@skons.net"


def test_mask_pii_phone_kr():
    assert mask_pii("010-1234-5678") == "010-****-****"


def test_mask_pii_none_passthrough():
    assert mask_pii(None) is None


def test_mask_pii_plain_string_unchanged():
    """email/phone 패턴 아님 → 원본 반환."""
    assert mask_pii("just a string") == "just a string"
