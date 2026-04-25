"""In-process plaintext DEK cache with TTL expiry.

Cache key includes a digest of encrypted_dek_b64 so that KMS key rotation
(which changes the encrypted form) automatically causes a cache miss.
"""

import hashlib
import threading
from typing import Callable

from cachetools import TTLCache

_lock = threading.Lock()
_cache: TTLCache = TTLCache(maxsize=2048, ttl=300)


def _key(file_id: int, encrypted_dek_b64: str) -> tuple:
    digest = hashlib.sha256(encrypted_dek_b64.encode()).hexdigest()[:16]
    return (file_id, digest)


def get_or_decrypt_dek(
    file_id: int,
    encrypted_dek_b64: str,
    decrypt_fn: Callable[[str], bytes],
) -> bytes:
    """Return cached plaintext DEK, or call decrypt_fn and cache the result."""
    k = _key(file_id, encrypted_dek_b64)
    with _lock:
        hit = _cache.get(k)
        if hit is not None:
            return hit
    plaintext_dek = decrypt_fn(encrypted_dek_b64)
    with _lock:
        _cache[k] = plaintext_dek
    return plaintext_dek
