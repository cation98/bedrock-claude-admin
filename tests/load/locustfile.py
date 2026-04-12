"""부하 테스트 — 1,000 concurrent user 시나리오.

실행:
  locust -f tests/load/locustfile.py \\
    --host https://api.skons.net \\
    --users 1000 \\
    --spawn-rate 50 \\
    --run-time 5m \\
    --headless

환경변수:
  LOCUST_TEST_TOKEN: 테스트용 JWT (또는 매 요청마다 신규 발급)
  LOCUST_WEBUI_HOST: Open WebUI 호스트
  LOCUST_AG_HOST: Bedrock AG 호스트

목표 SLO:
  - p95 응답시간 < 3s (TTFT 아님, 전체 응답)
  - 에러율 < 1%
  - 1,000 concurrent 유지 시간 5분
"""

import os
import json
import random
from locust import HttpUser, task, between, events


WEBUI_HOST = os.environ.get("LOCUST_WEBUI_HOST", "https://chat.skons.net")
AG_HOST = os.environ.get("LOCUST_AG_HOST", "https://api.skons.net")
TEST_TOKEN = os.environ.get("LOCUST_TEST_TOKEN", "")


class WebChatUser(HttpUser):
    """Open WebUI 웹챗 사용자 시뮬레이션."""

    wait_time = between(2, 10)  # 실제 사용자 패턴: 2~10초 사이 요청 간격

    def on_start(self):
        """세션 시작: JWT 획득 (환경변수 없으면 테스트 토큰 사용)."""
        self.token = TEST_TOKEN or self._get_test_token()
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def _get_test_token(self) -> str:
        """테스트용 JWT 획득 (실제 환경에서는 SSO 로그인 필요)."""
        # Phase 0: 테스트 환경에서는 고정 토큰 사용
        return "test-jwt-token-placeholder"

    @task(5)
    def chat_simple_query(self):
        """일반 채팅 요청 (가장 빈번한 시나리오, weight=5)."""
        messages = [
            "오늘의 날씨는 어떤가요?",
            "파이썬 리스트 comprehension을 설명해주세요.",
            "SQL JOIN의 종류를 알려주세요.",
            "비즈니스 이메일 작성법을 알려주세요.",
        ]

        with self.client.post(
            "/api/chat/completions",
            json={
                "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "messages": [{"role": "user", "content": random.choice(messages)}],
                "stream": False,
                "max_tokens": 100,
            },
            headers=self.headers,
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 429:
                resp.failure(f"Rate limited: {resp.text}")
            else:
                resp.failure(f"Error {resp.status_code}: {resp.text}")

    @task(2)
    def chat_streaming_query(self):
        """스트리밍 채팅 요청 (weight=2)."""
        with self.client.post(
            "/api/chat/completions",
            json={
                "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "messages": [{"role": "user", "content": "안녕하세요"}],
                "stream": True,
                "max_tokens": 50,
            },
            headers={**self.headers, "Accept": "text/event-stream"},
            stream=True,
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Streaming error {resp.status_code}")

    @task(1)
    def check_models(self):
        """모델 목록 조회 (정적 응답, weight=1)."""
        with self.client.get(
            "/api/models",
            headers=self.headers,
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Models error {resp.status_code}")


class JwtRefreshUser(HttpUser):
    """JWT refresh 패턴 시뮬레이션 (4분 주기 Pod 시나리오)."""

    wait_time = between(230, 250)  # ~4분 간격

    def on_start(self):
        self.refresh_token = os.environ.get("LOCUST_REFRESH_TOKEN", "test-refresh-token")

    @task
    def refresh_access_token(self):
        """Access JWT 갱신 (4분 주기 cron 시뮬레이션)."""
        with self.client.post(
            "/auth/refresh",
            json={"refresh_token": self.refresh_token},
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                body = resp.json()
                self.refresh_token = body.get("refresh_token", self.refresh_token)
                resp.success()
            elif resp.status_code == 401:
                resp.failure("Refresh token invalid/expired")
            else:
                resp.failure(f"Refresh error {resp.status_code}")


# ---------------------------------------------------------------------------
# 통계 훅
# ---------------------------------------------------------------------------

@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """요청별 SLO 위반 감지."""
    if response_time > 3000:  # 3s SLO
        print(
            f"[SLO BREACH] {request_type} {name}: {response_time:.0f}ms "
            f"(threshold: 3000ms)"
        )


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 SLO 요약 출력."""
    stats = environment.stats
    print("\n" + "=" * 60)
    print("부하 테스트 SLO 결과 요약")
    print("=" * 60)

    for name, stat in stats.entries.items():
        p95 = stat.get_response_time_percentile(0.95)
        err_pct = stat.fail_ratio * 100
        slo_pass = p95 < 3000 and err_pct < 1.0
        status = "✅ PASS" if slo_pass else "❌ FAIL"
        print(
            f"{status} {name[1]} {name[0]}: "
            f"p95={p95:.0f}ms, err={err_pct:.1f}%"
        )

    total_err = stats.total.fail_ratio * 100
    total_p95 = stats.total.get_response_time_percentile(0.95)
    print(f"\n전체: p95={total_p95:.0f}ms, 에러율={total_err:.1f}%")
    print("SLO 기준: p95 < 3000ms, 에러율 < 1%")
    print("=" * 60)
