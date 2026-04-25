"""Prometheus metrics scrape endpoint."""

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics():
    """Prometheus metrics scrape endpoint (내부 전용)."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
