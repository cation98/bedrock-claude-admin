"""Thread-safe Redis 싱글턴 클라이언트 + 분산 락.

Issue #7: 모듈 레벨 변수를 threading.Lock + double-checked locking으로 보호.
Issue #10: Lua 스크립트 기반 원자적 lock release — 락 소유자만 해제 가능.

Redis가 설정되지 않았거나 연결에 실패하면 None을 반환한다.
호출자는 None 반환 시 인메모리 fallback을 사용해야 한다.
"""

import logging
import os
import socket
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


# ==================== 프로세스 고유 식별자 ====================
# 분산 락에서 "누가 락을 잡았는가"를 구분하기 위한 owner_id.
# hostname + PID 조합으로 프로세스 간 고유성을 보장한다.

_OWNER_ID: str = f"{socket.gethostname()}:{os.getpid()}"


def get_owner_id() -> str:
    """현재 프로세스의 고유 식별자를 반환한다."""
    return _OWNER_ID


# ==================== Redis 분산 락 (Issue #10) ====================
# 멀티 레플리카 환경에서 스케줄러 중복 실행 방지를 위한 분산 락.
# Lua 스크립트로 원자적 check-and-delete를 보장하여
# 다른 프로세스의 락을 실수로 삭제하는 문제를 방지한다.

# Lua 스크립트: 락의 현재 값이 요청자의 owner_id와 일치할 때만 삭제
# KEYS[1] = 락 키, ARGV[1] = owner_id
# 반환값: 1 = 삭제 성공, 0 = 소유자 불일치 (삭제하지 않음)
RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def acquire_scheduler_lock_redis(
    lock_name: str, owner_id: str | None = None, ttl_seconds: int = 300
) -> bool:
    """Redis SETNX 기반 분산 락 획득.

    Redis가 사용 불가하면 인메모리 fallback(scheduler.py의 acquire_scheduler_lock)으로
    자동 전환한다.

    Args:
        lock_name: 락 이름 (고유 식별자).
        owner_id: 락 소유자 ID. None이면 현재 프로세스 ID 사용.
        ttl_seconds: 락 유효 시간 (초). 기본 300초 (5분).

    Returns:
        True이면 락 획득 성공, False이면 이미 락이 존재함.
    """
    if owner_id is None:
        owner_id = _OWNER_ID

    r = get_redis()
    if not r:
        # Redis 없음 → 인메모리 fallback
        from app.core.scheduler import acquire_scheduler_lock

        return acquire_scheduler_lock(lock_name, ttl_seconds)

    try:
        # SET key value NX EX ttl — 원자적으로 "키가 없을 때만" 설정
        result = r.set(f"lock:{lock_name}", owner_id, nx=True, ex=ttl_seconds)
        return bool(result)
    except Exception as e:
        logger.warning("Redis 락 획득 실패 — 인메모리 fallback: %s", e)
        from app.core.scheduler import acquire_scheduler_lock

        return acquire_scheduler_lock(lock_name, ttl_seconds)


def release_scheduler_lock_redis(
    lock_name: str, owner_id: str | None = None
) -> bool:
    """Redis Lua 스크립트 기반 원자적 락 해제.

    Lua 스크립트로 "현재 락 소유자가 나인 경우에만 삭제"를 원자적으로 수행한다.
    다른 프로세스가 잡은 락을 실수로 삭제하는 것을 방지한다 (Issue #10).

    Redis가 사용 불가하면 인메모리 fallback으로 자동 전환한다.

    Args:
        lock_name: 락 이름.
        owner_id: 락 소유자 ID. None이면 현재 프로세스 ID 사용.

    Returns:
        True이면 락 해제 성공, False이면 소유자 불일치 또는 락 없음.
    """
    if owner_id is None:
        owner_id = _OWNER_ID

    r = get_redis()
    if not r:
        # Redis 없음 → 인메모리 fallback
        from app.core.scheduler import release_scheduler_lock

        release_scheduler_lock(lock_name)
        return True

    try:
        # redis-py의 eval()은 Redis EVAL 명령어 — Lua 스크립트를 서버 측에서 실행
        # Python의 eval()과는 무관하며, 보안 위험 없음
        result = r.eval(RELEASE_LOCK_LUA, 1, f"lock:{lock_name}", owner_id)  # noqa: S307
        if not result:
            logger.debug(
                "락 해제 스킵 — 소유자 불일치: lock=%s, owner=%s",
                lock_name,
                owner_id,
            )
        return bool(result)
    except Exception as e:
        logger.warning("Redis 락 해제 실패 — 인메모리 fallback: %s", e)
        from app.core.scheduler import release_scheduler_lock

        release_scheduler_lock(lock_name)
        return True


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
