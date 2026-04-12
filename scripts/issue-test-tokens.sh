#!/bin/bash
# =============================================================================
# QA 테스트 토큰 발급 스크립트
#
# 목적:
#   CP-18 / CP-19 / CP-20 / CP-21 / CP-22 E2E 테스트 실행 전
#   TEST_USER_TOKEN + TEST_POD_TOKEN 을 ALLOW_TEST_USERS 우회 경로로 발급.
#   SSO / 2FA 없이 즉시 JWT 를 발급하므로 QA 환경에서만 사용.
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  SECURITY: ALLOW_TEST_USERS=true 는 QA/스테이징 환경 전용.              │
# │  프로덕션(prod) auth-gateway 에서는 이 환경변수를 절대 설정하지 마세요. │
# └─────────────────────────────────────────────────────────────────────────┘
#
# 필수 조건:
#   1. auth-gateway 실행 시 ALLOW_TEST_USERS=true 설정 (env ConfigMap)
#   2. TESTUSER01 계정이 users 테이블에 is_approved=true 로 존재
#      (없으면 "DB 사전 설정" 참고)
#   3. AUTH_GATEWAY_URL 접근 가능
#      - 클러스터 내부: http://auth-gateway.platform.svc.cluster.local:8000
#      - 로컬 포트포워드: kubectl port-forward -n platform svc/auth-gateway 8000:8000
#
# DB 사전 설정 (TESTUSER01 계정 없을 시):
#   psql "$DATABASE_URL" <<'SQL'
#   INSERT INTO users (username, name, role, is_approved, approved_at, created_at)
#   VALUES ('TESTUSER01', 'QA Test User', 'user', true, NOW(), NOW())
#   ON CONFLICT (username) DO UPDATE SET is_approved = true;
#   SQL
#
# 사용:
#   # TEST_USER_TOKEN 만 발급 (CP-18/20/21/22):
#   AUTH_GATEWAY_URL=http://localhost:8000 ./scripts/issue-test-tokens.sh
#
#   # TEST_POD_TOKEN 도 발급 (CP-19 포함):
#   AUTH_GATEWAY_URL=http://localhost:8000 \
#   DATABASE_URL=postgresql://user:pass@host:5432/platform \
#   ./scripts/issue-test-tokens.sh
#
#   # 발급 후 테스트 실행:
#   source .env.test
#   pytest tests/e2e/ -v --timeout=30
#
# 출력 파일: .env.test (git ignore 됨 — 커밋 금지)
# =============================================================================

set -euo pipefail

# ── 환경변수 기본값 ────────────────────────────────────────────────────────────
AUTH_GATEWAY_URL="${AUTH_GATEWAY_URL:-http://localhost:8000}"
DATABASE_URL="${DATABASE_URL:-}"
OPEN_WEBUI_URL="${OPEN_WEBUI_URL:-https://chat.skons.net}"
BEDROCK_AG_URL="${BEDROCK_AG_URL:-}"
ADMIN_DASHBOARD_URL="${ADMIN_DASHBOARD_URL:-}"

TEST_USERNAME="TESTUSER01"
TEST_PASSWORD="test2026"
TEST_POD_NAME="claude-terminal-testuser01"
OUTPUT_FILE=".env.test"

# ── 색상 ──────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}   $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC}  $*"; }

# ── 의존성 확인 ───────────────────────────────────────────────────────────────
for cmd in curl python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "'$cmd' 가 필요합니다. 설치 후 재시도하세요."
    exit 1
  fi
done

# JSON 파싱 헬퍼: jq 우선, 없으면 python3 fallback
json_get() {
  local json="$1" key="$2"
  if command -v jq >/dev/null 2>&1; then
    echo "$json" | jq -r ".${key} // empty"
  else
    python3 -c "
import json, sys
d = json.loads(sys.argv[1])
print(d.get(sys.argv[2], ''))
" "$json" "$key"
  fi
}

echo ""
echo "================================================================"
echo "  QA 테스트 토큰 발급 (ALLOW_TEST_USERS 우회)"
echo "  AUTH_GATEWAY_URL: ${AUTH_GATEWAY_URL}"
echo "================================================================"
echo ""

# =============================================================================
# STEP 1: auth-gateway 응답 확인
# =============================================================================
info "Step 0: auth-gateway health 확인..."

HEALTH_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  --connect-timeout 5 "${AUTH_GATEWAY_URL}/health" 2>/dev/null || echo "000")

if [ "$HEALTH_CODE" != "200" ]; then
  err "auth-gateway 접근 실패 (HTTP ${HEALTH_CODE})"
  err "AUTH_GATEWAY_URL=${AUTH_GATEWAY_URL} 에 연결할 수 없습니다."
  echo ""
  warn "포트포워드 방법:"
  warn "  kubectl port-forward -n platform svc/auth-gateway 8000:8000 &"
  warn "  export AUTH_GATEWAY_URL=http://localhost:8000"
  exit 1
fi

ok "auth-gateway 응답 정상"

# =============================================================================
# STEP 1: TEST_USER_TOKEN — SSO + 2FA 전체 우회
# =============================================================================
info "Step 1: TEST_USER_TOKEN 발급 (${TEST_USERNAME} / ${TEST_PASSWORD})"
info "       → POST ${AUTH_GATEWAY_URL}/api/v1/auth/login"

HTTP_RESPONSE=$(curl -s -w "\n%{http_code}" \
  --connect-timeout 10 \
  -X POST "${AUTH_GATEWAY_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${TEST_USERNAME}\",\"password\":\"${TEST_PASSWORD}\"}")

HTTP_CODE=$(echo "$HTTP_RESPONSE" | tail -n1)
RESP_BODY=$(echo "$HTTP_RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  # access_token 추출
  TEST_USER_TOKEN=$(json_get "$RESP_BODY" "access_token")
  if [ -z "$TEST_USER_TOKEN" ]; then
    err "access_token 파싱 실패."
    err "응답: ${RESP_BODY}"
    exit 1
  fi
  ok "TEST_USER_TOKEN 발급 완료 (${#TEST_USER_TOKEN} chars)"

elif [ "$HTTP_CODE" = "202" ]; then
  # 2FA 응답 — ALLOW_TEST_USERS 가 비활성화됨
  err "2FA 단계로 넘어감 (HTTP 202). ALLOW_TEST_USERS 가 비활성화되어 있습니다."
  echo ""
  warn "auth-gateway ConfigMap 또는 Deployment env 에서 확인:"
  warn "  kubectl get cm auth-gateway-env -n platform -o yaml | grep ALLOW_TEST"
  warn "  ALLOW_TEST_USERS=true 가 없으면 추가 후 rollout restart 필요"
  exit 1

elif [ "$HTTP_CODE" = "403" ]; then
  err "승인 거부 (HTTP 403). ${TEST_USERNAME} 계정이 미승인 상태입니다."
  echo ""
  warn "아래 SQL 로 계정을 승인 후 재시도:"
  warn "  psql \$DATABASE_URL -c \\"
  warn "  \"INSERT INTO users (username,name,role,is_approved,approved_at,created_at)\\"
  warn "   VALUES ('TESTUSER01','QA Test User','user',true,NOW(),NOW())\\"
  warn "   ON CONFLICT (username) DO UPDATE SET is_approved=true;\""
  exit 1

else
  err "로그인 실패 (HTTP ${HTTP_CODE})"
  err "응답: ${RESP_BODY}"
  exit 1
fi

# =============================================================================
# STEP 2: TEST_POD_TOKEN — 테스트 terminal_sessions 행 삽입 후 pod-token-exchange
# =============================================================================
TEST_POD_TOKEN=""

if [ -z "$DATABASE_URL" ]; then
  warn "Step 2: DATABASE_URL 미설정 → TEST_POD_TOKEN 발급 건너뜀"
  warn "       CP-19 테스트는 자동 skip 됩니다. 나머지 CP 는 그대로 실행 가능합니다."
else
  info "Step 2: TEST_POD_TOKEN 발급"
  info "  1) 랜덤 pod_token 생성"
  info "  2) terminal_sessions 행 upsert (pod_name=${TEST_POD_NAME})"
  info "  3) POST /auth/pod-token-exchange"

  # psql 확인
  if ! command -v psql >/dev/null 2>&1; then
    err "psql 이 필요합니다 (DATABASE_URL 사용 시). brew install libpq 또는 apt-get install postgresql-client"
    exit 1
  fi

  # ── 랜덤 pod token 생성 (32바이트 hex = 64자) ────────────────────────────────
  POD_TOKEN_PLAIN=$(python3 -c "import secrets; print(secrets.token_hex(32))")

  # SHA-256 해시 (python3 — macOS / Linux 공통)
  POD_TOKEN_HASH=$(python3 -c "
import hashlib, sys
print(hashlib.sha256(sys.argv[1].encode()).hexdigest())
" "$POD_TOKEN_PLAIN")

  # ── user_id 조회 ──────────────────────────────────────────────────────────────
  USER_ID=$(psql "$DATABASE_URL" -t -A -c \
    "SELECT id FROM users WHERE username = '${TEST_USERNAME}' AND is_approved = true LIMIT 1;" \
    2>/dev/null | head -1 | tr -d '[:space:]')

  if [ -z "$USER_ID" ]; then
    err "${TEST_USERNAME} 계정이 users 테이블에 없거나 is_approved=false 입니다."
    echo ""
    warn "아래 SQL 을 먼저 실행하세요:"
    warn "  psql \$DATABASE_URL <<'SQL'"
    warn "  INSERT INTO users (username,name,role,is_approved,approved_at,created_at)"
    warn "  VALUES ('TESTUSER01','QA Test User','user',true,NOW(),NOW())"
    warn "  ON CONFLICT (username) DO UPDATE SET is_approved=true;"
    warn "  SQL"
    exit 1
  fi

  ok "user_id=${USER_ID} 확인"

  # ── terminal_sessions upsert ─────────────────────────────────────────────────
  # pod-token-exchange 는 pod_token_hash 가 1회 사용 후 Redis blacklist 에 등록됨.
  # 스크립트 재실행 시 새 hash 로 덮어쓰므로 안전하게 재발급 가능.
  psql "$DATABASE_URL" -q -c "
    INSERT INTO terminal_sessions
      (user_id, username, pod_name, pod_status, pod_token_hash,
       session_type, started_at, created_at, last_active_at)
    VALUES
      (${USER_ID}, '${TEST_USERNAME}', '${TEST_POD_NAME}', 'running',
       '${POD_TOKEN_HASH}', 'workshop', NOW(), NOW(), NOW())
    ON CONFLICT (pod_name) DO UPDATE SET
      user_id        = EXCLUDED.user_id,
      username       = EXCLUDED.username,
      pod_status     = 'running',
      pod_token_hash = EXCLUDED.pod_token_hash,
      last_active_at = NOW();
  " 2>/dev/null

  ok "terminal_sessions upsert 완료 (pod_name=${TEST_POD_NAME})"

  # ── pod-token-exchange 호출 ──────────────────────────────────────────────────
  info "  → POST ${AUTH_GATEWAY_URL}/auth/pod-token-exchange"

  EXCHANGE_RESPONSE=$(curl -s -w "\n%{http_code}" \
    --connect-timeout 10 \
    -X POST "${AUTH_GATEWAY_URL}/auth/pod-token-exchange" \
    -H "Content-Type: application/json" \
    -d "{\"pod_token\":\"${POD_TOKEN_PLAIN}\",\"pod_name\":\"${TEST_POD_NAME}\"}")

  EXCHANGE_CODE=$(echo "$EXCHANGE_RESPONSE" | tail -n1)
  EXCHANGE_BODY=$(echo "$EXCHANGE_RESPONSE" | sed '$d')

  if [ "$EXCHANGE_CODE" != "200" ]; then
    err "pod-token-exchange 실패 (HTTP ${EXCHANGE_CODE})"
    err "응답: ${EXCHANGE_BODY}"
    exit 1
  fi

  TEST_POD_TOKEN=$(json_get "$EXCHANGE_BODY" "access_token")
  if [ -z "$TEST_POD_TOKEN" ]; then
    err "access_token 파싱 실패. 응답: ${EXCHANGE_BODY}"
    exit 1
  fi

  ok "TEST_POD_TOKEN 발급 완료 (${#TEST_POD_TOKEN} chars)"

  # 주의: pod-token-exchange 는 1회 사용 후 Redis 에 blacklist 등록됨.
  # CP-19 의 test_cp19_pod_token_exchange_integration 은 TEST_POD_TOKEN(plaintext) 을
  # 직접 pod-token-exchange 에 전달하므로, 위에서 이미 교환했다면 테스트가 실패함.
  # → 이 스크립트는 TEST_POD_TOKEN 을 교환된 JWT 로 세팅하지 않고
  #   새 plaintext token 을 DATABASE_URL 로 직접 DB 에 upsert 하여
  #   테스트 시점에 교환 가능한 상태를 만들어야 함.
  #
  # ★ CP-19 테스트를 실행하려면:
  #    TEST_POD_TOKEN 에 plaintext pod_token 을 넣어야 합니다.
  #    아래에서 TEST_POD_TOKEN_PLAIN 을 별도로 저장합니다.
  TEST_POD_TOKEN_PLAIN="$POD_TOKEN_PLAIN"

  # DB upsert → plaintext 가 미교환 상태여야 CP-19 가 교환을 성공시킬 수 있음.
  # 하지만 이미 위에서 한 번 exchange 했으므로 hash 가 blacklist 에 등록됨.
  # → CP-19 전용으로 신선한 (미교환) token 을 다시 upsert 합니다.
  POD_TOKEN_FRESH=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  POD_TOKEN_FRESH_HASH=$(python3 -c "
import hashlib, sys
print(hashlib.sha256(sys.argv[1].encode()).hexdigest())
" "$POD_TOKEN_FRESH")

  psql "$DATABASE_URL" -q -c "
    UPDATE terminal_sessions
    SET pod_token_hash = '${POD_TOKEN_FRESH_HASH}', last_active_at = NOW()
    WHERE pod_name = '${TEST_POD_NAME}';
  " 2>/dev/null

  TEST_POD_TOKEN="$POD_TOKEN_FRESH"
  ok "CP-19 용 신선한 TEST_POD_TOKEN 준비 완료 (미교환 상태)"
  warn "TEST_POD_TOKEN 은 JWT 가 아닌 plaintext bootstrap token 입니다."
  warn "CP-19 는 이 값을 /auth/pod-token-exchange 에 전달하여 교환 성공 여부를 검증합니다."
fi

# =============================================================================
# STEP 3: .env.test 파일 저장
# =============================================================================
info "Step 3: ${OUTPUT_FILE} 저장"

cat > "$OUTPUT_FILE" <<ENVFILE
# QA E2E 테스트 환경변수
# 생성: scripts/issue-test-tokens.sh ($(date +"%Y-%m-%d %H:%M:%S"))
# 적용: source .env.test
# 경고: 이 파일은 테스트 토큰을 포함합니다. .gitignore 에 등록되어 있습니다. 커밋 금지.

# ── 서비스 URL ──────────────────────────────────────────────────────────────
export AUTH_GATEWAY_URL="${AUTH_GATEWAY_URL}"
export OPEN_WEBUI_URL="${OPEN_WEBUI_URL}"
export BEDROCK_AG_URL="${BEDROCK_AG_URL}"
export ADMIN_DASHBOARD_URL="${ADMIN_DASHBOARD_URL}"

# ── 테스트 토큰 ─────────────────────────────────────────────────────────────
# TEST_USER_TOKEN: SSO+2FA 없이 발급된 일반 사용자 JWT (CP-18/20/21/22)
export TEST_USER_TOKEN="${TEST_USER_TOKEN}"

# TEST_POD_TOKEN: CP-19 용 plaintext bootstrap token
# pod-token-exchange 엔드포인트에 전달 → JWT 교환 성공 여부 검증
# 1회 교환 후 blacklist 등록 → 재사용 불가. 재발급 시 스크립트 재실행.
export TEST_POD_TOKEN="${TEST_POD_TOKEN}"
export TEST_POD_NAME="${TEST_POD_NAME}"

# ── 선택 토큰 (수동 설정) ───────────────────────────────────────────────────
# TEST_ADMIN_TOKEN: CP-22 (서버 취소 확인) — admin role JWT 필요
# export TEST_ADMIN_TOKEN=""
#
# OVER_BUDGET_USER_TOKEN: CP-20 E2E (월 예산 0 설정된 계정 JWT)
# export OVER_BUDGET_USER_TOKEN=""
ENVFILE

ok "${OUTPUT_FILE} 저장 완료"

# =============================================================================
# 실행 안내
# =============================================================================
echo ""
echo "================================================================"
echo "  완료! 아래 명령으로 E2E 테스트를 실행하세요:"
echo "================================================================"
echo ""
echo "  source ${OUTPUT_FILE}"
echo ""
echo "  # CP-18/21/22 (webchat + streaming — TEST_USER_TOKEN 만 필요):"
echo "  pytest tests/e2e/test_chat_flow.py::TestWebChatFlow -v"
echo "  pytest tests/e2e/test_websocket_streaming.py -v"
echo ""

if [ -n "$TEST_POD_TOKEN" ]; then
  echo "  # CP-19 (pod-token-exchange — TEST_POD_TOKEN 포함):"
  echo "  pytest tests/e2e/test_chat_flow.py::TestPodBootToBedrockFlow::test_cp19_pod_token_exchange_integration -v"
  echo ""
fi

echo "  # CP-20 (budget enforcement — unit mock):"
echo "  pytest tests/e2e/test_chat_flow.py::TestBudgetEnforcement::test_cp20_over_budget_returns_429_korean_message -v"
echo ""
echo "  # 전체 E2E:"
echo "  pytest tests/e2e/ -v --timeout=30"
echo ""

if [ -z "$TEST_POD_TOKEN" ]; then
  warn "TEST_POD_TOKEN 미발급 → CP-19 는 자동 skip 됩니다."
  warn "CP-19 실행 시: DATABASE_URL=\$DB_URL ./scripts/issue-test-tokens.sh"
  echo ""
fi
