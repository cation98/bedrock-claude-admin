"""웹앱 Auth Proxy.

/app/{pod_name}/ 경로의 요청을 인증 후 Pod port 3000으로 프록시.
사용자 Pod에 SSO 시크릿을 제공하지 않고, Auth Gateway가 인증을 대행.

인증된 요청에 X-User-Id, X-User-Name 헤더를 주입하여
사용자 웹앱에서 현재 접속자를 식별할 수 있도록 함.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.core.config import get_settings
from app.core.security import decode_token

router = APIRouter(tags=["app-proxy"])
logger = logging.getLogger(__name__)


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


@router.api_route(
    "/app/{pod_name}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_to_webapp(pod_name: str, path: str, request: Request):
    """사용자 웹앱으로 프록시 (인증 필수).

    흐름:
      Browser → Auth Gateway (JWT 검증) → X-User-Id 헤더 주입 → Pod webapp (port 3000)

    Pod의 webapp은 X-User-Id / X-User-Name 헤더만 읽으면 현재 접속자를 알 수 있다.
    SSO_CLIENT_SECRET이 Pod에 노출되지 않으므로 보안이 강화된다.
    """
    # 인증 확인
    user = await _get_user_from_request(request)
    if not user:
        # 미인증 → 로그인 페이지로 리다이렉트
        return RedirectResponse(url="/", status_code=302)

    # 대상 Pod 서비스 URL 구성
    # K8s 내부 DNS: {pod_name}.{namespace}.svc.cluster.local
    target_url = f"http://{pod_name}.claude-sessions:3000/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

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
            detail="웹앱이 실행되지 않았습니다. 터미널에서 포트 3000으로 앱을 실행해주세요.",
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="웹앱 응답 시간 초과")
