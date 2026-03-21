"""Bedrock Claude Auth Gateway — FastAPI Application.

사내 SSO 인증 → K8s Pod 관리 → 웹 터미널 프록시
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import Base, engine
from app.routers import auth, sessions

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


@app.on_event("startup")
async def startup():
    """앱 시작 시 DB 테이블 생성."""
    Base.metadata.create_all(bind=engine)
    logger.info(f"{settings.app_name} started")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "auth-gateway"}
