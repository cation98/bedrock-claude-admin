"""봇 토큰 암호화/복호화 유틸리티.

Fernet 대칭 암호화를 사용하여 봇 토큰을 안전하게 저장하고,
LRU 캐시로 반복 복호화 비용을 줄인다.

환경변수:
  BOT_ENCRYPTION_KEY — Fernet 키 (python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  프로덕션에서는 AWS Secrets Manager에서 로드.
"""

import hashlib
import os

from cryptography.fernet import Fernet


class BotCrypto:
    """봇 토큰 Fernet 암호화 + SHA-256 해싱."""

    def __init__(self, key: str | None = None):
        """Fernet 키 초기화.

        Args:
            key: Fernet 키 문자열. None이면 BOT_ENCRYPTION_KEY 환경변수에서 로드.
        """
        if key is None:
            key = os.environ.get("BOT_ENCRYPTION_KEY", "")
        if not key:
            raise ValueError("BOT_ENCRYPTION_KEY 환경변수가 설정되지 않았습니다.")
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt_token(self, plain_token: str) -> bytes:
        """봇 토큰을 Fernet으로 암호화.

        Returns:
            암호화된 바이트열 (DB BYTEA 컬럼에 저장).
        """
        return self._fernet.encrypt(plain_token.encode("utf-8"))

    def decrypt_token(self, encrypted: bytes) -> str:
        """암호화된 봇 토큰을 복호화.

        Returns:
            평문 봇 토큰 문자열.
        """
        return self._fernet.decrypt(encrypted).decode("utf-8")

    @staticmethod
    def hash_token(token: str) -> str:
        """봇 토큰의 SHA-256 해시 (webhook URL 라우팅 키).

        Returns:
            64자리 hex digest.
        """
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Dict 캐시: 복호화된 토큰을 메모리에 캐싱하여 매 webhook마다 Fernet 복호화 반복 방지.
# encrypted bytes (hex) → plain token 매핑 (최대 100개).
# ---------------------------------------------------------------------------

_decrypt_cache: dict[str, str] = {}


def get_cached_token(encrypted: bytes, crypto: BotCrypto) -> str:
    """복호화된 토큰 캐시. Fernet 키를 캐시 인자로 노출하지 않음."""
    hex_key = encrypted.hex()
    if hex_key not in _decrypt_cache:
        if len(_decrypt_cache) >= 100:
            # Remove oldest entry (FIFO eviction)
            _decrypt_cache.pop(next(iter(_decrypt_cache)))
        _decrypt_cache[hex_key] = crypto.decrypt_token(encrypted)
    return _decrypt_cache[hex_key]
