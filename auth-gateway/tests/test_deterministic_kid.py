"""Phase 1a: deterministic kid (SHA256 fingerprint) 회귀 방지."""
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.jwt_rs256 import _compute_kid


def _gen_pem() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def test_kid_is_16_hex_chars():
    pem = _gen_pem()
    kid = _compute_kid(pem)
    assert len(kid) == 16, f"kid length = {len(kid)}, expected 16"
    int(kid, 16)  # valid hex


def test_same_pem_same_kid():
    pem = _gen_pem()
    kid1 = _compute_kid(pem)
    kid2 = _compute_kid(pem)
    assert kid1 == kid2, "Same PEM must yield same kid (replica 간 일치 보장)"


def test_different_pem_different_kid():
    pem1 = _gen_pem()
    pem2 = _gen_pem()
    assert _compute_kid(pem1) != _compute_kid(pem2), "Different PEM must yield different kid"


def test_kid_matches_sha256_n_e():
    """공개키 fingerprint 기반 — JWKS n + e 재현 가능."""
    pem = _gen_pem()
    private = serialization.load_pem_private_key(pem, password=None)
    numbers = private.public_key().public_numbers()
    n_bytes = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    e_bytes = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    expected = hashlib.sha256(n_bytes + e_bytes).hexdigest()[:16]
    assert _compute_kid(pem) == expected, "kid must be SHA256(n||e)[:16]"
