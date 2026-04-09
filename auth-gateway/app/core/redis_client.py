"""Thread-safe Redis 싱글턴 클라이언트.

Issue #7: 모듈 레벨 변수를 threading.Lock + double-checked locking으로 보호.
Redis가 설정되지 않았거나 연결에 실패하면 None을 반환한다.
호출자는 None 반환 시 인메모리 fallback을 사용해야 한다.
"""

import logging
import threading

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis_lock = threading.Lock()
_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis | None:
    """Redis 클라이언트 싱글턴을 반환한다.

    - redis_url이 비어 있으면 None (Redis 비활성화).
    - 연결 실패 시 None을 반환하고, 다음 호출 시 재시도한다.
    - thread-safe: double-checked locking 패턴 적용.

    Returns:
        redis.Redis 인스턴스 또는 None.
    """
    global _redis_client

    # Fast path: 이미 연결된 클라이언트가 있으면 바로 반환
    if _redis_client is not None:
        return _redis_client

    with _redis_lock:
        # Double-check: 락 대기 중 다른 스레드가 이미 생성했을 수 있음
        if _redis_client is not None:
            return _redis_client

        settings = get_settings()
        if not settings.redis_url:
            logger.debug("redis_url이 설정되지 않음 — Redis 비활성화")
            return None

        try:
            client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                retry_on_timeout=True,
            )
            # 실제 연결 확인
            client.ping()
            _redis_client = client
            logger.info("Redis 연결 성공: %s", settings.redis_url)
            return _redis_client
        except Exception as e:
            logger.warning("Redis 연결 실패 — 인메모리 fallback 사용: %s", e)
            return None


def reset_redis() -> None:
    """Redis 클라이언트를 초기화한다 (테스트 또는 장애 복구용).

    다음 get_redis() 호출 시 재연결을 시도한다.
    """
    global _redis_client
    with _redis_lock:
        if _redis_client is not None:
            try:
                _redis_client.close()
            except Exception:
                pass
            _redis_client = None
            logger.info("Redis 클라이언트 초기화 완료")
