from fastapi import FastAPI

from app.config import settings
from app.routes.safety import router as safety_router

app = FastAPI(
    title=settings.app_name,
    description="안전관리 시스템 샘플 API - Claude Code 실습용",
    version="0.1.0",
)

app.include_router(safety_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "app": settings.app_name}
