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

import time
import hashlib
from collections import defaultdict
from functools import wraps

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


# ---------- Rate Limiter ----------

class RateLimiter:
    """IP 기반 요청 제한기.

    Args:
        max_requests: 허용 최대 요청 수
        window_seconds: 시간 윈도우 (초)
        key_func: 요청에서 식별키 추출 함수 (기본: IP)
    """

    def __init__(self, max_requests: int = 5, window_seconds: int = 300, key_func=None):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_func = key_func or self._default_key
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

    def check(self, request: Request) -> None:
        """요청 확인. 제한 초과 시 HTTPException(429) 발생."""
        key = self.key_func(request)
        self._cleanup(key)

        if len(self._store[key]) >= self.max_requests:
            retry_after = int(self.window_seconds - (time.time() - self._store[key][0]))
            raise HTTPException(
                status_code=429,
                detail=f"요청 횟수 초과. {retry_after}초 후 다시 시도하세요.",
                headers={"Retry-After": str(max(1, retry_after))},
            )

        self._store[key].append(time.time())

    def remaining(self, request: Request) -> int:
        """남은 요청 횟수."""
        key = self.key_func(request)
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
        # Server 헤더 제거 (정보 노출 방지)
        response.headers.pop("Server", None)
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
    app.add_middleware(SecurityHeadersMiddleware)

    if login_rate_limit:
        app.add_middleware(
            LoginRateLimitMiddleware,
            max_requests=max_login_attempts,
            window_seconds=300,
        )
