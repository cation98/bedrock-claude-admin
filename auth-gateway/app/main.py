"""Bedrock Claude Auth Gateway — FastAPI Application.

사내 SSO 인증 → K8s Pod 관리 → 웹 터미널 프록시
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.database import Base, engine
<<<<<<< HEAD

from app.routers import auth, sessions, users, sms
=======
from app.routers import auth, sessions, app_proxy
>>>>>>> worktree-agent-a21aaa6c

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="AWS Bedrock Claude Code 사내 활용 플랫폼 — 인증 및 세션 관리",
    version="0.1.0",
)

# CORS 설정 (Admin Dashboard에서 API 호출 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 프로덕션에서는 실제 도메인으로 제한
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(auth.router)
app.include_router(sessions.router)
# app_proxy는 catch-all 경로(/app/{pod_name}/{path:path})를 갖기 때문에 반드시 마지막에 등록
app.include_router(app_proxy.router)

app.include_router(users.router)
app.include_router(sms.router)
app.include_router(sms.router)


@app.on_event("startup")
async def startup():
    """앱 시작 시 DB 테이블 생성."""
    Base.metadata.create_all(bind=engine)
    logger.info(f"{settings.app_name} started")


# 정적 파일 (로그인 페이지 등)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def root():
    """루트 → 로그인 페이지."""
    return FileResponse(str(static_dir / "login.html"))


@app.get("/health")
async def health():
    return {"status": "ok", "service": "auth-gateway"}
