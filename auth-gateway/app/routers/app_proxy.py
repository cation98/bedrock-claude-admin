"""웹앱 Auth Proxy.

/app/{pod_name}/ 경로의 요청을 인증 후 Pod 웹앱 포트로 프록시.
사용자 Pod에 SSO 시크릿을 제공하지 않고, Auth Gateway가 인증을 대행.

인증된 요청에 X-User-Id, X-User-Name 헤더를 주입하여
사용자 웹앱에서 현재 접속자를 식별할 수 있도록 함.

추가:
  GET /api/v1/apps/offline — NGINX custom error page용 오프라인 HTML 반환.
  Pod가 내려간 상태에서 NGINX가 502/503을 받으면 이 페이지를 사용자에게 표시.
"""

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models.app import DeployedApp

router = APIRouter(tags=["app-proxy"])
logger = logging.getLogger(__name__)

_OFFLINE_HTML = Path(__file__).resolve().parent.parent / "static" / "offline.html"


async def _get_user_from_request(request: Request) -> dict | None:
    """쿠키 또는 Authorization 헤더에서 사용자 정보 추출 (실패 시 None).

    JWT 토큰을 두 곳에서 순서대로 찾는다:
      1. Authorization: Bearer <token> 헤더
      2. claude_token 쿠키 (웹 브라우저 접속 시)
    """
    settings = get_settings()

    # 1) Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        payload = decode_token(token, settings)
        if payload is not None:
            return payload

    # 2) Cookie (claude_token)
    token = request.cookies.get("claude_token", "")
    if token:
        payload = decode_token(token, settings)
        if payload is not None:
            return payload

    return None


@router.get("/api/v1/apps/offline")
async def apps_offline_page():
    """NGINX custom error page용 오프라인 HTML 반환.

    NGINX Ingress 설정에서 proxy_intercept_errors + error_page 502/503
    으로 이 엔드포인트를 지정하면, Pod가 내려간 상태에서 사용자에게
    친절한 오프라인 안내 페이지를 보여줄 수 있다.
    """
    return FileResponse(_OFFLINE_HTML, media_type="text/html")


@router.api_route(
    "/app/{pod_name}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_to_webapp(
    pod_name: str, path: str, request: Request, db: Session = Depends(get_db)
):
    """사용자 웹앱으로 프록시 (인증 필수).

    흐름:
      Browser → Auth Gateway (JWT 검증) → X-User-Id 헤더 주입 → Pod webapp (app_port)

    Pod의 webapp은 X-User-Id / X-User-Name 헤더만 읽으면 현재 접속자를 알 수 있다.
    SSO_CLIENT_SECRET이 Pod에 노출되지 않으므로 보안이 강화된다.
    """
    # 인증 확인
    user = await _get_user_from_request(request)
    if not user:
        # 미인증 → 로그인 페이지로 리다이렉트
        return RedirectResponse(url="/", status_code=302)

    # deployed_apps에서 앱 포트 조회 (미등록 앱은 기본 3000 폴백)
    app_row = db.query(DeployedApp).filter(
        DeployedApp.pod_name == pod_name,
    ).first()
    app_port = app_row.app_port if app_row and app_row.app_port else 3000

    # _port 쿼리 파라미터로 포트 오버라이드 (3000-3100 범위만 허용)
    port_param = request.query_params.get("_port")
    if port_param and port_param.isdigit() and 3000 <= int(port_param) <= 3100:
        app_port = int(port_param)

    # 대상 Pod 서비스 URL 구성
    # K8s 내부 DNS: {pod_name}.{namespace}.svc.cluster.local
    target_url = f"http://{pod_name}.claude-sessions:{app_port}/{path}"
    # _port 파라미터를 제거한 쿼리스트링 전달
    query_pairs = [(k, v) for k, v in request.query_params.items() if k != "_port"]
    if query_pairs:
        qs = "&".join(f"{k}={v}" for k, v in query_pairs)
        target_url += f"?{qs}"

    # 원본 헤더 복사 + 인증 헤더 주입
    headers = dict(request.headers)
    headers["X-User-Id"] = user.get("sub", "")
    headers["X-User-Name"] = user.get("name", user.get("sub", ""))
    headers["X-Forwarded-For"] = request.client.host if request.client else ""
    # Host 헤더 제거 (프록시 대상으로 전달 시 충돌 방지)
    headers.pop("host", None)

    # 요청 본문 읽기
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
            )

        # 응답 반환 (hop-by-hop 헤더 제외)
        excluded_headers = {"transfer-encoding", "content-encoding", "content-length"}
        resp_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in excluded_headers
        }

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=resp_headers,
            media_type=response.headers.get("content-type"),
        )

    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"웹앱이 실행되지 않았습니다. 터미널에서 포트 {app_port}으로 앱을 실행해주세요.",
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="웹앱 응답 시간 초과")
