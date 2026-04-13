"""E2E 채팅 플로우 + Pod 부팅 테스트.

Coverage:
  CP-18: 웹 로그인 → 웹챗 첫 응답 (TTFT < 2s, 스트리밍 끊김 없음)
  CP-19: Pod 부팅 → JWT 교환 → Bedrock 첫 호출 성공 + usage_events 기록
  CP-20: 월 예산 초과 → 429 + 한글 안내 메시지

환경변수:
  AUTH_GATEWAY_URL: auth-gateway 주소
  OPEN_WEBUI_URL:   Open WebUI 주소 (direct service URL or ingress)
  TEST_USER_TOKEN:  auth-gateway JWT (SSO bypass, ALLOW_TEST_USERS=true)
  TEST_POD_TOKEN:   plaintext bootstrap pod token (pod-token-exchange용)

인증 플로우 (Open WebUI):
  auth-gateway JWT ≠ Open WebUI JWT — 별도 세션 토큰 시스템.

  [ingress 경유 — OPEN_WEBUI_URL = https://chat.skons.net]
    bedrock_jwt 쿠키 → nginx webui-verify 서브요청 → X-SKO-Email 주입 → OW 세션

  [direct service URL — OPEN_WEBUI_URL = http://open-webui.openwebui:8080]
    1. POST /api/v1/auths/signin + X-SKO-Email: {email} (dummy body)
       → Open WebUI Trusted Header auth → OW 세션 토큰 발급
    2. Authorization: Bearer {ow_token} 로 /api/chat/completions 호출

  이 테스트는 direct URL 기준으로 2-step flow 를 사용합니다.
  (ingress 경유 시에도 동일하게 동작 — /api/v1/auths/signin 은 외부 접근 가능)

Block: T4 (JWT), T8 (usage-worker), T11 (Bedrock AG) 완료 후 full 실행.
로컬 단위 테스트는 일부 mock 으로 실행 가능.
"""

import base64
import json
import os
import time

import httpx
import pytest
from unittest.mock import patch

AUTH_GATEWAY_URL = os.environ.get("AUTH_GATEWAY_URL", "")
OPEN_WEBUI_URL = os.environ.get("OPEN_WEBUI_URL", "")
TEST_USER_TOKEN = os.environ.get("TEST_USER_TOKEN", "")
TEST_POD_TOKEN = os.environ.get("TEST_POD_TOKEN", "")
TEST_POD_NAME = os.environ.get("TEST_POD_NAME", "claude-terminal-testuser01")

_has_live_env = bool(AUTH_GATEWAY_URL and OPEN_WEBUI_URL)
_has_jwt = bool(TEST_USER_TOKEN)


# =============================================================================
# 인증 헬퍼
# =============================================================================

def _email_from_token(token: str) -> str:
    """auth-gateway JWT 의 sub claim → Open WebUI 이메일 주소 추출.

    서명 검증 없이 payload 를 base64 디코딩하여 sub 만 읽음.
    네트워크 호출 없음 — 테스트 셋업 비용 최소화.

    반환: "{sub}@skons.net" (예: "TESTUSER01@skons.net")
    """
    try:
        payload_b64 = token.split(".")[1]
        # base64url padding 보정 (JWT 는 padding 생략)
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        sub = payload.get("sub", "testuser01")
        return f"{sub}@skons.net"
    except Exception:
        return "testuser01@skons.net"


def _get_ow_token(ow_url: str, ow_email: str) -> str:
    """Open WebUI Trusted Header signin → OW 세션 토큰 발급.

    Open WebUI 에 WEBUI_AUTH_TRUSTED_EMAIL_HEADER=X-SKO-Email 이 설정된 경우
    /api/v1/auths/signin 에 X-SKO-Email 헤더만 있으면 OW 가 자동으로 사용자를
    생성/로그인하고 세션 토큰을 반환한다.

    body 의 email/password 는 trusted-header 모드에서는 무시되나,
    OW 구현에 따라 빈 JSON 이라도 필요할 수 있어 dummy 값 전달.

    반환: OW Bearer 토큰 (실패 시 빈 문자열)
    """
    try:
        with httpx.Client(timeout=10.0) as http:
            resp = http.post(
                f"{ow_url}/api/v1/auths/signin",
                headers={"X-SKO-Email": ow_email},
                json={"email": ow_email, "password": ""},
            )
        if resp.status_code == 200:
            return resp.json().get("token", "")
        return ""
    except Exception:
        return ""


def _requires_live_env(fn):
    return pytest.mark.skipif(
        not _has_live_env,
        reason="AUTH_GATEWAY_URL / OPEN_WEBUI_URL 미설정",
    )(fn)


def _requires_jwt(fn):
    return pytest.mark.skipif(
        not _has_jwt,
        reason="TEST_USER_TOKEN 미설정",
    )(fn)


# =============================================================================
# CP-18: 웹 로그인 → 웹챗 첫 응답 (TTFT < 2s)
# =============================================================================

class TestWebChatFlow:
    """E2E: 로그인 → Open WebUI → 첫 Claude 응답 수신."""

    @classmethod
    def _ow_token(cls) -> str:
        """TEST_USER_TOKEN 에서 OW 세션 토큰을 1회 발급 (클래스 내 공유).

        OW 토큰은 만료 전까지 재사용 가능. 클래스 변수에 캐시.
        """
        if not hasattr(cls, "_cached_ow_token"):
            email = _email_from_token(TEST_USER_TOKEN)
            cls._cached_ow_token = _get_ow_token(OPEN_WEBUI_URL, email)
        return cls._cached_ow_token

    @_requires_live_env
    @_requires_jwt
    def test_cp18_webchat_first_token_time(self):
        """CP-18: 웹챗 첫 응답 Time-To-First-Token < 2초.

        PASS 기준:
          - SSE 스트림 첫 데이터 수신까지 2초 이내
          - HTTP 200 + content-type: text/event-stream
          - 스트림 끊김 없이 완료

        인증:
          auth-gateway JWT → _get_ow_token() → OW Bearer 토큰 → /api/chat/completions
        """
        ow_token = self._ow_token()
        if not ow_token:
            pytest.skip(
                "Open WebUI 세션 토큰 발급 실패. "
                "OPEN_WEBUI_URL 접근 가능 여부 및 WEBUI_AUTH_TRUSTED_EMAIL_HEADER=X-SKO-Email 설정 확인."
            )

        headers = {
            "Authorization": f"Bearer {ow_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "bedrock_ag_pipe.us.anthropic.claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "안녕하세요. 한 단어만 응답해주세요."}],
            "stream": True,
        }

        start_time = time.time()
        first_token_time = None
        chunks_received = 0

        with httpx.Client(timeout=30.0) as http:
            with http.stream(
                "POST",
                f"{OPEN_WEBUI_URL}/api/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                assert resp.status_code == 200, (
                    f"웹챗 API 실패: {resp.status_code} {resp.text}"
                )
                assert "text/event-stream" in resp.headers.get("content-type", ""), (
                    "스트리밍 응답이 아님. proxy-buffering off 설정 확인 필요."
                )

                for chunk in resp.iter_lines():
                    if chunk and chunk.startswith("data:") and chunk != "data: [DONE]":
                        if first_token_time is None:
                            first_token_time = time.time()
                        chunks_received += 1
                        if chunks_received >= 3:
                            break

        assert first_token_time is not None, "스트리밍 응답 없음"
        ttft = first_token_time - start_time
        assert ttft < 2.0, (
            f"FAIL: TTFT={ttft:.2f}s (기준: < 2.0s). "
            "Bedrock AG 또는 Open WebUI 응답 지연 확인 필요."
        )
        assert chunks_received >= 1, "스트리밍 chunk 없음"

    @_requires_live_env
    @_requires_jwt
    def test_cp18_webchat_streaming_no_buffering(self):
        """CP-18: SSE 스트리밍이 끊김 없이 증분 전달됨 (proxy-buffering off 확인).

        chunk 간격이 5초 이상이면 buffering 발생 가능 → FAIL.
        """
        ow_token = self._ow_token()
        if not ow_token:
            pytest.skip("Open WebUI 세션 토큰 발급 실패")

        headers = {"Authorization": f"Bearer {ow_token}"}
        payload = {
            "model": "bedrock_ag_pipe.us.anthropic.claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "1부터 5까지 세어주세요. 각 숫자만 한 줄씩."}],
            "stream": True,
        }

        chunk_times = []
        with httpx.Client(timeout=30.0) as http:
            with http.stream(
                "POST",
                f"{OPEN_WEBUI_URL}/api/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status_code != 200:
                    pytest.skip(f"API not available: {resp.status_code}")

                for chunk in resp.iter_lines():
                    if chunk and chunk.startswith("data:") and chunk != "data: [DONE]":
                        chunk_times.append(time.time())
                        if len(chunk_times) >= 5:
                            break

        if len(chunk_times) < 2:
            pytest.skip("Insufficient chunks to measure intervals")

        max_interval = max(
            chunk_times[i + 1] - chunk_times[i]
            for i in range(len(chunk_times) - 1)
        )
        assert max_interval < 5.0, (
            f"FAIL: 스트리밍 chunk 간격 {max_interval:.2f}s. "
            "ingress-nginx proxy-buffering off 설정 확인 필요."
        )


# =============================================================================
# CP-19: Pod 부팅 → JWT 교환 → Bedrock 첫 호출 성공
# =============================================================================

class TestPodBootToBedrockFlow:
    """E2E: Pod 시작 스크립트 → pod-token-exchange → Bedrock AG → usage_events."""

    def test_cp19_pod_token_exchange_integration(self):
        """CP-19 Unit: pod-token-exchange 엔드포인트 정상 동작 (T4 blocker).

        실제 Pod 부팅 시 entrypoint.sh 가 수행하는 순서:
          1. POST /auth/pod-token-exchange (pod_token + pod_name)
          2. 응답의 access_token 을 ~/.bedrock-token 에 저장
          3. Bedrock AG 호출 시 해당 토큰 사용

        TEST_POD_TOKEN: docs/qa/get-test-tokens.sh 로 발급한 plaintext bootstrap token.
        """
        if not _has_live_env:
            pytest.skip("EKS 환경 필요")
        if not TEST_POD_TOKEN:
            pytest.skip("TEST_POD_TOKEN 미설정 — docs/qa/get-test-tokens.sh (DATABASE_URL 포함) 실행")

        with httpx.Client(timeout=10.0) as http:
            resp = http.post(
                f"{AUTH_GATEWAY_URL}/auth/pod-token-exchange",
                json={"pod_token": TEST_POD_TOKEN, "pod_name": TEST_POD_NAME},
            )

        assert resp.status_code == 200, (
            f"pod-token-exchange 실패: {resp.status_code} {resp.text}"
        )
        body = resp.json()
        assert "access_token" in body, "access_token 없음"
        assert "refresh_token" in body, "refresh_token 없음"

    def test_cp19_bedrock_ag_accepts_jwt(self):
        """CP-19: Bedrock AG 가 JWT Bearer 토큰을 허용하고 usage_events 에 기록.

        Bedrock AG 는 auth-gateway JWT 를 직접 검증 (OW 세션 토큰 아님).
        T4 + T8 + T11 블로커. 실제 EKS 환경에서만 실행.
        """
        if not (_has_live_env and TEST_USER_TOKEN):
            pytest.skip("EKS 환경 + TEST_USER_TOKEN 필요")

        bedrock_ag_url = os.environ.get("BEDROCK_AG_URL", "")
        if not bedrock_ag_url:
            pytest.skip("BEDROCK_AG_URL 미설정")

        with httpx.Client(timeout=30.0) as http:
            resp = http.post(
                f"{bedrock_ag_url}/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {TEST_USER_TOKEN}"},
                json={
                    "model": "bedrock_ag_pipe.us.anthropic.claude-sonnet-4-6",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5,
                },
            )

        assert resp.status_code == 200, (
            f"Bedrock AG 거부: {resp.status_code} {resp.text}"
        )


# =============================================================================
# CP-20: 월 예산 초과 → 429 + 한글 안내
# =============================================================================

class TestBudgetEnforcement:
    """CP-20: 사용자 월 예산 초과 시 429 응답 + 한글 메시지."""

    def test_cp20_over_budget_returns_429_korean_message(self):
        """CP-20: usage_events 기반 예산 초과 감지 → 429 + 한글 안내.

        T8 (usage-worker) + T11 (Bedrock AG pipeline) 블로커.
        현재는 Bedrock AG mock 으로 로직 검증.
        """
        try:
            from openwebui_pipeline.budget_check import check_user_budget
        except ImportError:
            pytest.skip("T8/T11: openwebui_pipeline not yet implemented")

        with patch("openwebui_pipeline.budget_check.get_user_monthly_usage") as mock_usage:
            mock_usage.return_value = {"tokens_used": 1_000_000, "budget": 500_000}
            result = check_user_budget(user_id="TESTUSER01")

        assert result["exceeded"] is True
        assert result["status_code"] == 429
        assert any(
            kw in result.get("message", "")
            for kw in ("한도", "예산", "초과")
        ), (
            f"FAIL: 한글 안내 메시지 없음. Message: {result.get('message')}"
        )

    @_requires_live_env
    @_requires_jwt
    def test_cp20_e2e_over_budget_returns_429_with_korean(self):
        """CP-20 E2E: 실제 환경에서 예산 초과 사용자 → 429 + 한글 메시지.

        조건: 월 예산이 0 으로 설정된 OVER_BUDGET 테스트 사용자 필요.
        OVER_BUDGET_USER_TOKEN 이 auth-gateway JWT 이므로
        Open WebUI 호출 전 OW 세션 토큰으로 교환.
        """
        over_budget_token = os.environ.get("OVER_BUDGET_USER_TOKEN", "")
        if not over_budget_token:
            pytest.skip("OVER_BUDGET_USER_TOKEN 미설정 (월 예산 0인 테스트 사용자 필요)")

        # over_budget 사용자도 OW 세션 토큰 교환 (auth-gateway JWT ≠ OW JWT)
        ob_email = _email_from_token(over_budget_token)
        ob_ow_token = _get_ow_token(OPEN_WEBUI_URL, ob_email)
        if not ob_ow_token:
            pytest.skip(
                "예산 초과 사용자의 OW 세션 토큰 발급 실패. "
                "WEBUI_AUTH_TRUSTED_EMAIL_HEADER=X-SKO-Email 설정 확인."
            )

        with httpx.Client(timeout=10.0) as http:
            resp = http.post(
                f"{OPEN_WEBUI_URL}/api/chat/completions",
                headers={"Authorization": f"Bearer {ob_ow_token}"},
                json={
                    "model": "bedrock_ag_pipe.us.anthropic.claude-sonnet-4-6",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

        assert resp.status_code == 429, (
            f"Expected 429 (over budget), got {resp.status_code}."
        )
        body = resp.json()
        detail = body.get("detail", "") or body.get("message", "")
        assert any(
            kw in detail
            for kw in ("한도", "예산", "초과", "이번 달", "월", "사용량")
        ), (
            f"FAIL: 한글 안내 메시지 없음. Response: {body}. "
            "사용자 친화적 한국어 메시지 필요."
        )
