#!/bin/bash
# =============================================================================
# QA 전용 — 테스트 토큰 발급 진입점
#
# CP-18 / CP-19 / CP-20 / CP-21 / CP-22 E2E 실행 전 실행하세요.
# 내부적으로 scripts/issue-test-tokens.sh 를 호출합니다.
#
# ┌────────────────────────────────────────────────────────────────────────┐
# │  사전 조건                                                              │
# │  1. auth-gateway ConfigMap에 ALLOW_TEST_USERS=true 설정됨             │
# │     확인: kubectl get cm auth-gateway-env -n platform -o yaml          │
# │             | grep ALLOW_TEST                                          │
# │  2. TESTUSER01 계정이 users 테이블에 is_approved=true 로 존재          │
# │     없으면 아래 "DB 사전 설정" 실행                                    │
# │  3. auth-gateway 접근 가능 (포트포워드 또는 cluster-internal)          │
# └────────────────────────────────────────────────────────────────────────┘
#
# ── 빠른 시작 (CP-18/20/21/22 — DATABASE_URL 불필요) ──────────────────────
#
#   # 터미널 1: port-forward
#   kubectl port-forward -n platform svc/auth-gateway 8000:8000
#
#   # 터미널 2: 토큰 발급 + 테스트
#   AUTH_GATEWAY_URL=http://localhost:8000 ./docs/qa/get-test-tokens.sh
#   source .env.test
#   pytest tests/e2e/ -v --timeout=30 -k "not cp19"
#
# ── CP-19 포함 전체 실행 (DATABASE_URL 필요) ──────────────────────────────
#
#   AUTH_GATEWAY_URL=http://localhost:8000 \
#   DATABASE_URL=postgresql://user:pass@host:5432/platform \
#   ./docs/qa/get-test-tokens.sh
#   source .env.test
#   pytest tests/e2e/ -v --timeout=30
#
# ── DB 사전 설정 (TESTUSER01 없을 시 1회 실행) ────────────────────────────
#
#   psql "$DATABASE_URL" <<'SQL'
#   INSERT INTO users (username, name, role, is_approved, approved_at, created_at)
#   VALUES ('TESTUSER01', 'QA Test User', 'user', true, NOW(), NOW())
#   ON CONFLICT (username) DO UPDATE SET is_approved = true;
#   SQL
#
# ── CP별 TOKEN 요약 ───────────────────────────────────────────────────────
#
#   CP-18  웹챗 TTFT + 스트리밍   → TEST_USER_TOKEN
#   CP-19  pod-token-exchange      → TEST_POD_TOKEN (DATABASE_URL 필요)
#   CP-20  월 예산 초과 429        → TEST_USER_TOKEN (+ OVER_BUDGET_USER_TOKEN 선택)
#   CP-21  SSE 헤더 + buffering    → TEST_USER_TOKEN
#   CP-22  탭 닫기 요청 취소       → TEST_USER_TOKEN (+ TEST_ADMIN_TOKEN 선택)
#
# ── Locust 부하 테스트 ────────────────────────────────────────────────────
#
#   source .env.test
#   locust -f tests/load/locustfile.py \
#     --host "$OPEN_WEBUI_URL" \
#     --users 50 --spawn-rate 5 \
#     --headless --run-time 5m
#
# =============================================================================

set -euo pipefail

# 이 스크립트 위치에서 프로젝트 루트로 이동
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MAIN_SCRIPT="${PROJECT_ROOT}/scripts/issue-test-tokens.sh"

if [ ! -x "$MAIN_SCRIPT" ]; then
  echo "[ERR] ${MAIN_SCRIPT} 가 없거나 실행 권한이 없습니다."
  echo "      git pull 후 chmod +x scripts/issue-test-tokens.sh 실행"
  exit 1
fi

# 모든 환경변수를 그대로 전달하여 main 스크립트 실행
exec "$MAIN_SCRIPT" "$@"
