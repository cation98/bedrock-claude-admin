"""부하 테스트 — Phase 1b 50-user SLO 검증.

대상: auth-gateway (https://claude.skons.net)
엔드포인트: /health, /api/v1/sessions/, /api/v1/guides/, /api/v1/skills/

실행:
  LOCUST_TEST_TOKEN=<jwt> locust -f tests/load/locustfile.py \\
    --host https://claude.skons.net \\
    --users 50 \\
    --spawn-rate 5 \\
    --run-time 5m \\
    --headless \\
    --csv /tmp/locust-phase1b

환경변수:
  LOCUST_TEST_TOKEN: 테스트용 JWT (issue-test-tokens.sh 로 발급)
  LOCUST_AUTH_MODE: cookie (기본) | bearer — 기본은 브라우저 동작 재현(bedrock_jwt 쿠키).
                   Pod 내부/CLI 시뮬레이션이 필요하면 bearer로 전환.
  LOCUST_WEBUI_HOST: 미사용 (Phase 0 호환 유지)
  LOCUST_AG_HOST: 미사용

Phase 1b SLO:
  - p95 응답시간 < 150ms (auth-gateway 단순 조회 기준)
  - 에러율 < 1%
  - 50 concurrent users / spawn-rate 5 / 5분

Phase 0 SLO (레거시 참고):
  - p95 응답시간 < 3s (Open WebUI AI 응답 포함)
  - 에러율 < 1%

Phase 1 백로그 #16:
  portal.html은 apiFetch()에서 credentials:'include' + Authorization 헤더 제거를
  사용하므로 부하 테스트도 기본을 쿠키 기반(bedrock_jwt)으로 통일한다.
"""

import os
import random
from locust import HttpUser, task, between, events


TEST_TOKEN = os.environ.get("LOCUST_TEST_TOKEN", "")
AUTH_MODE = os.environ.get("LOCUST_AUTH_MODE", "cookie").lower()

# Phase 1b SLO 기준 (ms)
PHASE1B_P95_SLO = 150


class AuthGatewayUser(HttpUser):
    """Auth Gateway 사용자 시뮬레이션 — 50-user Phase 1b."""

    wait_time = between(2, 8)  # 실제 사용자 패턴: 2~8초 사이 요청 간격

    def on_start(self):
        """세션 시작: portal.html 동작 재현.

        - cookie 모드(기본): bedrock_jwt 쿠키 주입 → 매 요청에 자동 포함.
          security.get_current_user가 bedrock_jwt 쿠키를 우선 읽는다.
        - bearer 모드: Authorization 헤더 fallback (Pod 내부 / CLI 시나리오).
        """
        self.token = TEST_TOKEN
        if AUTH_MODE == "bearer":
            self.headers = {"Authorization": f"Bearer {self.token}"}
        else:
            self.headers = {}
            # Locust HttpUser는 requests.Session 기반 — cookies가 모든 요청에 자동 전송됨
            self.client.cookies.set("bedrock_jwt", self.token)

    @task(5)
    def health_check(self):
        """/health 엔드포인트 (인증 불필요, weight=5).

        가장 가벼운 엔드포인트 — auth-gateway 기본 응답 지연 측정.
        """
        with self.client.get(
            "/health",
            catch_response=True,
            name="/health",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Health check failed: {resp.status_code} {resp.text[:100]}")

    @task(4)
    def list_sessions(self):
        """/api/v1/sessions/ (JWT 인증 필요, weight=4).

        사용자 세션 목록 조회 — DB 조회 포함, 실제 부하 패턴.
        """
        with self.client.get(
            "/api/v1/sessions/",
            headers=self.headers,
            catch_response=True,
            name="/api/v1/sessions/",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 401:
                resp.failure(f"Unauthorized: token invalid/expired")
            elif resp.status_code == 403:
                resp.failure(f"Forbidden: {resp.text[:100]}")
            else:
                resp.failure(f"Sessions error {resp.status_code}: {resp.text[:100]}")

    @task(2)
    def list_guides(self):
        """/api/v1/guides/ (JWT 인증 필요, weight=2).

        가이드 목록 조회 — 읽기 전용 정적 응답.
        """
        with self.client.get(
            "/api/v1/guides/",
            headers=self.headers,
            catch_response=True,
            name="/api/v1/guides/",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code in (401, 403):
                resp.failure(f"Auth error {resp.status_code}")
            else:
                resp.failure(f"Guides error {resp.status_code}: {resp.text[:100]}")

    @task(1)
    def list_skills(self):
        """/api/v1/skills/ (JWT 인증 필요, weight=1).

        스킬 목록 조회 — 읽기 전용.
        """
        with self.client.get(
            "/api/v1/skills/",
            headers=self.headers,
            catch_response=True,
            name="/api/v1/skills/",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code in (401, 403):
                resp.failure(f"Auth error {resp.status_code}")
            else:
                resp.failure(f"Skills error {resp.status_code}: {resp.text[:100]}")


# ---------------------------------------------------------------------------
# 통계 훅
# ---------------------------------------------------------------------------

@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """요청별 SLO 위반 감지 (Phase 1b: 150ms)."""
    if response_time > PHASE1B_P95_SLO * 3:  # 3배 초과 시 경고 (450ms)
        print(
            f"[SLOW] {request_type} {name}: {response_time:.0f}ms "
            f"(p95 SLO: {PHASE1B_P95_SLO}ms)"
        )


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 SLO 요약 출력 (Phase 1b 기준)."""
    stats = environment.stats
    print("\n" + "=" * 60)
    print("Phase 1b 부하 테스트 SLO 결과 요약")
    print(f"SLO 기준: p95 < {PHASE1B_P95_SLO}ms, 에러율 < 1%")
    print("=" * 60)

    for name, stat in stats.entries.items():
        p95 = stat.get_response_time_percentile(0.95)
        err_pct = stat.fail_ratio * 100
        slo_pass = p95 < PHASE1B_P95_SLO and err_pct < 1.0
        status = "PASS" if slo_pass else "FAIL"
        print(
            f"[{status}] {name[1]} {name[0]}: "
            f"p95={p95:.0f}ms, err={err_pct:.1f}%, RPS={stat.current_rps:.1f}"
        )

    total_err = stats.total.fail_ratio * 100
    total_p95 = stats.total.get_response_time_percentile(0.95)
    total_p99 = stats.total.get_response_time_percentile(0.99)
    total_max = stats.total.max_response_time
    overall_pass = total_p95 < PHASE1B_P95_SLO and total_err < 1.0
    print(f"\n전체: p95={total_p95:.0f}ms, p99={total_p99:.0f}ms, max={total_max:.0f}ms, 에러율={total_err:.1f}%")
    print(f"최종 판정: {'PASS' if overall_pass else 'FAIL'}")
    print("=" * 60)
