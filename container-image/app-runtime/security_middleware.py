"""웹앱 보안 미들웨어 — OWASP Top 10 대응.

사용법 (FastAPI):
    from security_middleware import add_security

    app = FastAPI()
    add_security(app)  # 한 줄로 전체 보안 적용

    # 또는 개별 적용:
    from security_middleware import RateLimiter

    login_limiter = RateLimiter(max_requests=5, window_seconds=300)

    @app.post("/login")
    async def login(request: Request):
        login_limiter.check(request)  # 5분간 5회 초과 시 429 에러
        ...
"""

import logging
import time
import hashlib
from collections import defaultdict
from functools import wraps

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


def _try_get_redis():
    """Redis 클라이언트를 가져온다. 모듈이 없거나 실패하면 None."""
    try:
        from app.core.redis_client import get_redis
        return get_redis()
    except Exception:
        return None


# ---------- Rate Limiter ----------

class RateLimiter:
    """IP 기반 요청 제한기.

    Redis가 사용 가능하면 Redis로 rate limiting을 수행한다.
    Redis가 불가하면 인메모리 dict로 fallback한다 (Issue #9).

    Args:
        max_requests: 허용 최대 요청 수
        window_seconds: 시간 윈도우 (초)
        key_func: 요청에서 식별키 추출 함수 (기본: IP)
        redis_prefix: Redis 키 접두사
    """

    def __init__(
        self,
        max_requests: int = 5,
        window_seconds: int = 300,
        key_func=None,
        redis_prefix: str = "ratelimit",
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_func = key_func or self._default_key
        self.redis_prefix = redis_prefix
        # 인메모리 fallback store (Redis 불가 시 사용)
        self._store: dict[str, list[float]] = defaultdict(list)

    @staticmethod
    def _default_key(request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
        return ip

    def _cleanup(self, key: str) -> None:
        now = time.time()
        cutoff = now - self.window_seconds
        self._store[key] = [t for t in self._store[key] if t > cutoff]

    def _redis_key(self, key: str) -> str:
        return f"{self.redis_prefix}:{key}"

    def _check_redis(self, r, key: str) -> None:
        """Redis sorted set 기반 rate limit 체크."""
        redis_key = self._redis_key(key)
        now = time.time()
        cutoff = now - self.window_seconds

        pipe = r.pipeline()
        # 만료된 엔트리 제거
        pipe.zremrangebyscore(redis_key, 0, cutoff)
        # 현재 윈도우의 요청 수 조회
        pipe.zcard(redis_key)
        results = pipe.execute()
        count = results[1]

        if count >= self.max_requests:
            # 가장 오래된 요청의 타임스탬프 조회
            oldest = r.zrange(redis_key, 0, 0, withscores=True)
            if oldest:
                retry_after = int(self.window_seconds - (now - oldest[0][1]))
            else:
                retry_after = self.window_seconds
            raise HTTPException(
                status_code=429,
                detail=f"요청 횟수 초과. {max(1, retry_after)}초 후 다시 시도하세요.",
                headers={"Retry-After": str(max(1, retry_after))},
            )

        # 현재 요청 기록
        pipe2 = r.pipeline()
        pipe2.zadd(redis_key, {f"{now}": now})
        pipe2.expire(redis_key, self.window_seconds)
        pipe2.execute()

    def _check_memory(self, key: str) -> None:
        """인메모리 fallback rate limit 체크."""
        self._cleanup(key)

        if len(self._store[key]) >= self.max_requests:
            retry_after = int(self.window_seconds - (time.time() - self._store[key][0]))
            raise HTTPException(
                status_code=429,
                detail=f"요청 횟수 초과. {max(1, retry_after)}초 후 다시 시도하세요.",
                headers={"Retry-After": str(max(1, retry_after))},
            )

        self._store[key].append(time.time())

    def check(self, request: Request) -> None:
        """요청 확인. 제한 초과 시 HTTPException(429) 발생.

        Issue #9: 매 호출마다 get_redis()를 호출하여 Redis 복구 시 자동 전환.
        Redis 실패 시 해당 요청만 인메모리 fallback 사용.
        """
        key = self.key_func(request)

        # 매 호출마다 Redis 클라이언트를 새로 가져옴 (캐싱하지 않음)
        r = _try_get_redis()
        if r is not None:
            try:
                self._check_redis(r, key)
                return
            except HTTPException:
                raise  # 429는 그대로 전파
            except Exception as e:
                logger.warning("Redis rate limit 실패, 인메모리 fallback: %s", e)

        # Redis 불가 시 인메모리 fallback
        self._check_memory(key)

    def remaining(self, request: Request) -> int:
        """남은 요청 횟수."""
        key = self.key_func(request)

        r = _try_get_redis()
        if r is not None:
            try:
                redis_key = self._redis_key(key)
                now = time.time()
                cutoff = now - self.window_seconds
                r.zremrangebyscore(redis_key, 0, cutoff)
                count = r.zcard(redis_key)
                return max(0, self.max_requests - count)
            except Exception:
                pass

        self._cleanup(key)
        return max(0, self.max_requests - len(self._store[key]))


# ---------- Security Headers Middleware ----------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """응답에 보안 헤더 자동 추가."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # 검색 엔진 크롤러 차단 (사내 웹앱이 외부에 인덱싱되지 않도록)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        # Server 헤더 제거 (정보 노출 방지)
        if "server" in response.headers:
            del response.headers["server"]
        return response


# ---------- Login Rate Limit Middleware ----------

class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    """로그인 경로에 대한 자동 rate limiting.

    기본 설정: /login, /auth, /signin 경로에 5분간 5회 제한.
    """

    def __init__(self, app, paths=None, max_requests=5, window_seconds=300):
        super().__init__(app)
        self.paths = paths or ["/login", "/auth", "/signin", "/api/login", "/api/auth"]
        self.limiter = RateLimiter(max_requests=max_requests, window_seconds=window_seconds)

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and any(request.url.path.startswith(p) for p in self.paths):
            try:
                self.limiter.check(request)
            except HTTPException as e:
                return JSONResponse(
                    status_code=e.status_code,
                    content={"detail": e.detail},
                    headers=e.headers or {},
                )
        return await call_next(request)


# ---------- File Upload Validation ----------

ALLOWED_EXTENSIONS = {
    ".csv", ".xlsx", ".xls", ".json", ".txt", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".zip", ".tar", ".gz",
}

BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".sh", ".ps1", ".vbs",
    ".dll", ".so", ".dylib", ".bin",
    ".php", ".jsp", ".asp", ".aspx", ".cgi",
}

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB


def validate_upload(filename: str, content_length: int = 0) -> None:
    """업로드 파일 검증. 위험한 확장자 차단 + 크기 제한.

    Raises:
        HTTPException: 차단된 파일 또는 크기 초과 시
    """
    import os
    ext = os.path.splitext(filename)[1].lower()

    if ext in BLOCKED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"차단된 파일 형식: {ext}")

    if content_length > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"파일 크기 초과 (최대 {MAX_UPLOAD_SIZE // (1024*1024)}MB)",
        )


# ---------- 통합 적용 함수 ----------

def add_security(app, login_rate_limit: bool = True, max_login_attempts: int = 5):
    """FastAPI 앱에 보안 미들웨어 일괄 적용.

    Args:
        app: FastAPI 인스턴스
        login_rate_limit: 로그인 rate limiting 활성화 여부
        max_login_attempts: 로그인 최대 시도 횟수 (5분 기준)

    사용 예:
        app = FastAPI()
        add_security(app)
    """
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(SecurityHeadersMiddleware)

    # CORS 설정: 사내 플랫폼이므로 모든 오리진 허용하되
    # allow_credentials=False로 크로스-오리진 쿠키/인증 헤더 차단
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
        allow_credentials=False,  # 크로스-오리진 쿠키 차단
    )

    if login_rate_limit:
        app.add_middleware(
            LoginRateLimitMiddleware,
            max_requests=max_login_attempts,
            window_seconds=300,
        )
