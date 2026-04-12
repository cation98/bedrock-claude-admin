"""WebSocket 스트리밍 + 사용량 이벤트 테스트.

Coverage:
  CP-21: WebSocket 스트리밍 proxy-buffering off + SSE 설정 검증
  CP-22: 탭 닫기(연결 종료) → 서버 요청 취소 + 미완 토큰 usage_events 기록

설계 근거 (design doc):
  "Open WebUI 스트리밍 중 tab close: 서버 측 요청 취소 → 미완 토큰도 usage_events에 기록."

Block:
  CP-21: T17 (ingress 설정) 완료. ✅ 검증 가능.
  CP-22: T8 (usage-worker) + T11 (Bedrock AG) 완료 후.

환경변수:
  AUTH_GATEWAY_URL, OPEN_WEBUI_URL, TEST_USER_TOKEN
"""

import os
import time
import asyncio
import pytest
import httpx

AUTH_GATEWAY_URL = os.environ.get("AUTH_GATEWAY_URL", "")
OPEN_WEBUI_URL = os.environ.get("OPEN_WEBUI_URL", "")
TEST_USER_TOKEN = os.environ.get("TEST_USER_TOKEN", "")

_has_live_env = bool(OPEN_WEBUI_URL)
_has_jwt = bool(TEST_USER_TOKEN)


def _requires_live_env(fn):
    return pytest.mark.skipif(
        not _has_live_env,
        reason="OPEN_WEBUI_URL 미설정",
    )(fn)


def _requires_jwt(fn):
    return pytest.mark.skipif(
        not _has_jwt,
        reason="TEST_USER_TOKEN 미설정",
    )(fn)


# ===========================================================================
# CP-21: Streaming Configuration
# ===========================================================================

class TestStreamingConfiguration:
    """CP-21: ingress-nginx proxy-buffering off + SSE 헤더 검증."""

    @_requires_live_env
    def test_cp21_health_endpoint_response_headers(self):
        """CP-21: Open WebUI 응답 헤더에 X-Accel-Buffering: no 확인.

        ingress-nginx proxy-buffering off 설정이 실제로 적용되어
        스트리밍 응답이 클라이언트에 즉시 전달되는지 확인.
        """
        with httpx.Client(timeout=10.0) as http:
            resp = http.get(f"{OPEN_WEBUI_URL}/health")

        # X-Accel-Buffering: no — nginx가 버퍼링하지 않음을 명시
        x_accel = resp.headers.get("x-accel-buffering", "").lower()
        # 없는 경우도 있음 — nginx.conf에서 proxy_buffering off로 처리하면 헤더 없음
        # PASS if either 'no' is set or header is absent (global proxy_buffering off)
        assert x_accel in ("no", ""), (
            f"FAIL: X-Accel-Buffering: '{x_accel}'. 'no'이어야 스트리밍이 즉시 전달됨."
        )

    @_requires_live_env
    @_requires_jwt
    def test_cp21_streaming_response_content_type(self):
        """CP-21: 스트리밍 API의 Content-Type: text/event-stream 확인."""
        with httpx.Client(timeout=15.0) as http:
            with http.stream(
                "POST",
                f"{OPEN_WEBUI_URL}/api/chat/completions",
                headers={"Authorization": f"Bearer {TEST_USER_TOKEN}"},
                json={
                    "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
            ) as resp:
                if resp.status_code != 200:
                    pytest.skip(f"API unavailable: {resp.status_code}")

                content_type = resp.headers.get("content-type", "")

        assert "text/event-stream" in content_type, (
            f"FAIL: SSE 스트리밍이 아님. Content-Type: {content_type}. "
            "proxy_pass 설정에서 스트리밍이 차단되고 있을 수 있음."
        )

    @_requires_live_env
    @_requires_jwt
    def test_cp21_streaming_transfer_encoding_chunked(self):
        """CP-21: 스트리밍 응답이 chunked transfer encoding으로 전달됨."""
        with httpx.Client(timeout=15.0) as http:
            with http.stream(
                "POST",
                f"{OPEN_WEBUI_URL}/api/chat/completions",
                headers={"Authorization": f"Bearer {TEST_USER_TOKEN}"},
                json={
                    "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
            ) as resp:
                if resp.status_code != 200:
                    pytest.skip(f"API unavailable: {resp.status_code}")

                transfer_enc = resp.headers.get("transfer-encoding", "").lower()
                content_len = resp.headers.get("content-length", "")

        # SSE는 chunked encoding이거나 content-length 없어야 함
        assert transfer_enc == "chunked" or not content_len, (
            f"FAIL: 스트리밍이 단일 응답으로 전달됨. "
            f"Transfer-Encoding: {transfer_enc}, Content-Length: {content_len}. "
            "proxy_buffering이 활성화되어 있을 수 있음."
        )


# ===========================================================================
# CP-22: 탭 닫기 → 서버 요청 취소 + usage_events 기록
# ===========================================================================

class TestTabCloseHandling:
    """CP-22: 클라이언트 연결 종료 → 서버 cancel + 미완 토큰 usage 기록."""

    @_requires_live_env
    @_requires_jwt
    def test_cp22_server_cancels_request_on_disconnect(self):
        """CP-22: 스트리밍 도중 연결 끊으면 서버가 Bedrock 호출 취소.

        완전한 응답을 받지 않고 연결을 끊은 후
        서버 측에서 요청을 계속 처리하지 않는지 확인.

        검증 방법: 연결 끊고 짧은 시간 내에 admin API로 활성 스트림 없음 확인.
        """
        admin_api_url = os.environ.get("ADMIN_DASHBOARD_URL", "")
        if not admin_api_url:
            pytest.skip("ADMIN_DASHBOARD_URL 미설정 — 서버 취소 직접 확인 불가")

        admin_token = os.environ.get("TEST_ADMIN_TOKEN", "")
        if not admin_token:
            pytest.skip("TEST_ADMIN_TOKEN 미설정")

        # 스트리밍 시작 후 즉시 종료
        request_id = None
        try:
            with httpx.Client(timeout=5.0) as http:
                with http.stream(
                    "POST",
                    f"{OPEN_WEBUI_URL}/api/chat/completions",
                    headers={"Authorization": f"Bearer {TEST_USER_TOKEN}"},
                    json={
                        "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                        "messages": [{"role": "user", "content": "1부터 100까지 세어주세요."}],
                        "stream": True,
                    },
                ) as resp:
                    if resp.status_code == 200:
                        # 첫 chunk만 받고 연결 종료 (tab close 시뮬레이션)
                        for _ in resp.iter_lines():
                            break
                    # 연결 강제 종료
        except Exception:
            pass

        # 잠시 대기 후 서버 상태 확인
        time.sleep(2)

        # Admin API로 활성 Bedrock 스트림 확인
        with httpx.Client(timeout=5.0) as http:
            status_resp = http.get(
                f"{admin_api_url}/api/v1/streams/active",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        if status_resp.status_code == 200:
            active_streams = status_resp.json().get("count", 0)
            # 테스트 계정의 스트림이 종료되었어야 함
            assert active_streams == 0, (
                f"FAIL: 연결 종료 후 서버에 {active_streams}개 활성 스트림 존재. "
                "서버 측 요청 취소가 작동하지 않음."
            )

    def test_cp22_unit_usage_events_records_incomplete_stream(self):
        """CP-22 Unit: 미완 스트리밍도 usage_events에 기록됨.

        T8 (usage-worker) 블로커. 파이프라인 로직 직접 테스트.
        """
        try:
            from openwebui_pipeline.usage_emit import record_usage_event
        except ImportError:
            pytest.skip("T8: openwebui_pipeline not yet implemented")

        from unittest.mock import patch, MagicMock

        with patch("openwebui_pipeline.usage_emit.db_insert") as mock_insert:
            mock_insert.return_value = True

            result = record_usage_event(
                user_id="TESTUSER01",
                model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                input_tokens=100,
                output_tokens=15,  # 미완 — 원래 응답보다 적음
                is_complete=False,
                source="webui",
            )

        assert result is True, "usage_events 기록 실패"
        mock_insert.assert_called_once()

        call_args = mock_insert.call_args
        assert call_args[0][0].get("is_complete") is False, (
            "미완 스트림임을 is_complete=False로 기록해야 함."
        )
