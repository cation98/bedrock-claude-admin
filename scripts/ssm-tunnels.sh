#!/usr/bin/env bash
# SSM Port Forwarding — 로컬 개발용 RDS 터널
#
# 프로덕션 RDS 인스턴스를 SSM bastion을 통해 localhost에 포워딩하고,
# Docker Desktop K8s secret을 자동으로 패치하여 auth-gateway Pod이 tunnel을 사용하게 함.
#
# 터널 구성:
#   safety-prod-db-readonly → localhost:5434  (WORKSHOP_DATABASE_URL)
#   aiagentdb               → localhost:5435  (TANGO_DATABASE_URL, DOCULOG_DATABASE_URL, DOCULOG_DB_PASSWORD)
#
# 사용법:
#   ./scripts/ssm-tunnels.sh start   # 터널 시작 + K8s secret 패치
#   ./scripts/ssm-tunnels.sh stop    # 터널 종료 + K8s secret 초기화
#   ./scripts/ssm-tunnels.sh status  # 터널/연결 상태 확인
#
# 사전 요구사항:
#   - aws CLI v2 설치 및 aws sso login (ap-northeast-2 profile)
#   - session-manager-plugin 설치: brew install --cask session-manager-plugin
#   - Docker Desktop Kubernetes 활성화
#   - 단일 터널 수동 테스트 완료 (docs/LOCAL-DEV-SETUP.md 참조)

set -euo pipefail

# ============================================================
# 설정
# ============================================================
readonly BASTION_INSTANCE="i-01af38d47bfa846a6"  # sko-bastion (SG: sg-0e02b0a3b0295c7c5)
readonly AWS_REGION="ap-northeast-2"

readonly SAFETY_RDS_HOST="safety-prod-db-readonly.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com"
readonly AIAGENT_RDS_HOST="aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com"
readonly SAFETY_LOCAL_PORT=5434    # safety DB  → WORKSHOP_DATABASE_URL
readonly AIAGENT_LOCAL_PORT=5435   # aiagentdb  → TANGO + DOCULOG (동일 호스트, 다른 DB)

readonly PIDS_FILE="/tmp/bedrock-ssm-tunnels.pids"
readonly K8S_SECRET="auth-gateway-secrets"
readonly K8S_NS="platform"
readonly K8S_CONTEXT="docker-desktop"

# ============================================================
# 헬퍼
# ============================================================

log()  { echo "==> $*"; }
info() { echo "    $*"; }

# PostgreSQL URL의 host:port를 host.docker.internal:NEW_PORT으로 교체하고 sslmode=disable 강제
# (localhost 터널에서는 SSL cert CN 미스매치로 sslmode=require 불가)
rewrite_url_for_tunnel() {
  local url="$1" new_port="$2"
  python3 - "$url" "$new_port" <<'EOF'
import re, sys
url, port = sys.argv[1], sys.argv[2]
# host:port 교체 (@ 뒤의 host:숫자)
url = re.sub(r'@[^/:@]+:\d+/', f'@host.docker.internal:{port}/', url)
# 기존 sslmode 파라미터 제거
url = re.sub(r'[?&]sslmode=[^&]*', '', url)
url = url.rstrip('?&')
# sslmode=disable 추가
url += ('&' if '?' in url else '?') + 'sslmode=disable'
print(url)
EOF
}

# prod K8s context 자동 탐지 (docker-desktop 제외)
detect_prod_context() {
  kubectl config get-contexts -o name 2>/dev/null | grep -v docker-desktop | head -1 || true
}

# prod K8s secret에서 base64 디코딩된 값 추출
get_prod_secret() {
  local ctx="$1" key="$2"
  kubectl get secret "$K8S_SECRET" -n "$K8S_NS" --context "$ctx" \
    -o jsonpath="{.data.${key}}" 2>/dev/null | base64 -d 2>/dev/null || true
}

# ============================================================
# 자격증명 소스 (start / patch 공통)
# ============================================================
load_credentials() {
  PROD_CTX=$(detect_prod_context)
  WORKSHOP_URL="" TANGO_URL="" DOCULOG_URL="" DOCULOG_PW=""

  if [ -n "$PROD_CTX" ]; then
    info "prod K8s context: $PROD_CTX"
    WORKSHOP_URL=$(get_prod_secret "$PROD_CTX" "WORKSHOP_DATABASE_URL")
    TANGO_URL=$(get_prod_secret "$PROD_CTX" "TANGO_DATABASE_URL")
    DOCULOG_URL=$(get_prod_secret "$PROD_CTX" "DOCULOG_DATABASE_URL")
    DOCULOG_PW=$(get_prod_secret "$PROD_CTX" "DOCULOG_DB_PASSWORD")
  else
    info "prod context 없음 — auth-gateway/.env 폴백 사용"
    local env_file
    env_file="$(git rev-parse --show-toplevel)/auth-gateway/.env"
    if [ ! -f "$env_file" ]; then
      echo "ERROR: $env_file 없음. prod K8s context도 없고 .env도 없음."
      exit 1
    fi
    WORKSHOP_URL=$(grep -E '^WORKSHOP_DATABASE_URL=' "$env_file" | cut -d= -f2- | tr -d '"' || true)
    TANGO_URL=$(grep -E '^TANGO_DATABASE_URL=' "$env_file" | cut -d= -f2- | tr -d '"' || true)
  fi

  if [ -z "$WORKSHOP_URL" ] && [ -z "$TANGO_URL" ]; then
    echo "ERROR: WORKSHOP_DATABASE_URL / TANGO_DATABASE_URL를 가져오지 못함."
    exit 1
  fi
}

# K8s secret 패치 (platform 네임스페이스가 없으면 경고만 — local-dev-up.sh 후 patch로 재실행)
do_patch() {
  local workshop_url="$1" tango_url="$2" doculog_url="$3" doculog_pw="$4"

  log "로컬 K8s secret 패치 중..."
  local patch_data="{"

  if [ -n "$workshop_url" ]; then
    local tw; tw=$(rewrite_url_for_tunnel "$workshop_url" "$SAFETY_LOCAL_PORT")
    patch_data+="\"WORKSHOP_DATABASE_URL\":\"$tw\","
    info "WORKSHOP_DATABASE_URL → $tw"
  fi

  if [ -n "$tango_url" ]; then
    local tt; tt=$(rewrite_url_for_tunnel "$tango_url" "$AIAGENT_LOCAL_PORT")
    patch_data+="\"TANGO_DATABASE_URL\":\"$tt\","
    info "TANGO_DATABASE_URL → $tt"
  fi

  if [ -n "$doculog_url" ]; then
    local td; td=$(rewrite_url_for_tunnel "$doculog_url" "$AIAGENT_LOCAL_PORT")
    patch_data+="\"DOCULOG_DATABASE_URL\":\"$td\","
    info "DOCULOG_DATABASE_URL → $td"
  fi

  if [ -n "$doculog_pw" ]; then
    patch_data+="\"DOCULOG_DB_PASSWORD\":\"$doculog_pw\","
    info "DOCULOG_DB_PASSWORD 패치됨"
  fi

  patch_data="${patch_data%,}}"

  if kubectl patch secret "$K8S_SECRET" -n "$K8S_NS" --context "$K8S_CONTEXT" \
    --type merge -p "{\"stringData\":$patch_data}" 2>&1; then
    info "secret 패치 완료"
  else
    echo ""
    echo "⚠️  K8s secret 패치 실패 — platform 네임스페이스가 아직 없습니다."
    echo "   local-dev-up.sh를 먼저 실행하여 K8s 환경을 구성한 후:"
    echo "   ./scripts/ssm-tunnels.sh patch"
    echo ""
    echo "   (터널 자체는 정상 실행 중입니다)"
  fi
}

# ============================================================
# start
# ============================================================
cmd_start() {
  # 1. Docker Desktop K8s 확인
  log "Docker Desktop Kubernetes 확인..."
  if ! kubectl cluster-info --context "$K8S_CONTEXT" >/dev/null 2>&1; then
    echo "ERROR: Docker Desktop Kubernetes가 실행되지 않음."
    echo "       Docker Desktop → Settings → Kubernetes → Enable Kubernetes"
    exit 1
  fi
  info "OK"

  # 2. 이미 실행 중이면 중단
  if [ -f "$PIDS_FILE" ]; then
    log "터널이 이미 실행 중입니다 ($(cat "$PIDS_FILE")). 먼저 'stop'을 실행하세요."
    exit 0
  fi

  # 3. 자격증명 소스 결정
  log "자격증명 소스 탐지..."
  load_credentials

  # 4. SSM 터널 시작 (백그라운드)
  log "SSM 터널 1 시작 → safety-prod-db-readonly (localhost:$SAFETY_LOCAL_PORT)..."
  aws ssm start-session \
    --target "$BASTION_INSTANCE" \
    --document-name AWS-StartPortForwardingSessionToRemoteHost \
    --parameters "{\"host\":[\"$SAFETY_RDS_HOST\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"$SAFETY_LOCAL_PORT\"]}" \
    --region "$AWS_REGION" &
  PID_SAFETY=$!

  log "SSM 터널 2 시작 → aiagentdb (localhost:$AIAGENT_LOCAL_PORT)..."
  aws ssm start-session \
    --target "$BASTION_INSTANCE" \
    --document-name AWS-StartPortForwardingSessionToRemoteHost \
    --parameters "{\"host\":[\"$AIAGENT_RDS_HOST\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"$AIAGENT_LOCAL_PORT\"]}" \
    --region "$AWS_REGION" &
  PID_AIAGENT=$!

  echo "$PID_SAFETY $PID_AIAGENT" > "$PIDS_FILE"
  info "PID 저장: $PIDS_FILE"

  # 5. 터널 안정화 대기
  log "터널 안정화 대기 (5초)..."
  sleep 5

  # 연결 확인
  if ! nc -z localhost "$SAFETY_LOCAL_PORT" 2>/dev/null; then
    echo "WARNING: localhost:$SAFETY_LOCAL_PORT 연결 안 됨 (SSM 세션 수립에 시간이 더 필요할 수 있음)"
  fi
  if ! nc -z localhost "$AIAGENT_LOCAL_PORT" 2>/dev/null; then
    echo "WARNING: localhost:$AIAGENT_LOCAL_PORT 연결 안 됨"
  fi

  # 6. K8s secret 패치 (platform 네임스페이스가 아직 없으면 경고만 — local-dev-up.sh 후 patch 서브커맨드로 재실행)
  do_patch "$WORKSHOP_URL" "$TANGO_URL" "$DOCULOG_URL" "$DOCULOG_PW"

  echo ""
  echo "✅ SSM 터널 시작 완료"
  echo ""
  echo "   auth-gateway Pod 재시작 (새 env 적용):"
  echo "   kubectl rollout restart deployment/auth-gateway -n $K8S_NS --context $K8S_CONTEXT"
  echo ""
  echo "   터널 중지:"
  echo "   ./scripts/ssm-tunnels.sh stop"
}

# ============================================================
# stop
# ============================================================
cmd_stop() {
  if [ -f "$PIDS_FILE" ]; then
    read -r PID_SAFETY PID_AIAGENT < "$PIDS_FILE" || true
    log "SSM 터널 종료 (PID: $PID_SAFETY $PID_AIAGENT)..."
    kill "$PID_SAFETY" "$PID_AIAGENT" 2>/dev/null || true
    rm -f "$PIDS_FILE"
  fi

  # 잔여 프로세스 정리
  pkill -f "ssm start-session.*ForwardingSession" 2>/dev/null || true

  # K8s secret 초기화 (tunnel URL 제거)
  if kubectl cluster-info --context "$K8S_CONTEXT" >/dev/null 2>&1; then
    kubectl patch secret "$K8S_SECRET" -n "$K8S_NS" --context "$K8S_CONTEXT" \
      --type merge -p \
      '{"stringData":{"WORKSHOP_DATABASE_URL":"","TANGO_DATABASE_URL":"","DOCULOG_DATABASE_URL":"","DOCULOG_DB_PASSWORD":""}}' \
      2>/dev/null || true
    info "K8s secret 초기화 완료"
  fi

  echo "✅ SSM 터널 종료"
}

# ============================================================
# status
# ============================================================
cmd_status() {
  echo "=== SSM 터널 상태 ==="
  if [ -f "$PIDS_FILE" ]; then
    read -r PID_SAFETY PID_AIAGENT < "$PIDS_FILE" || true
    if kill -0 "$PID_SAFETY" 2>/dev/null; then
      echo "  safety-prod-db-readonly → localhost:$SAFETY_LOCAL_PORT  [실행중 PID=$PID_SAFETY]"
    else
      echo "  safety-prod-db-readonly → localhost:$SAFETY_LOCAL_PORT  [종료됨]"
    fi
    if kill -0 "$PID_AIAGENT" 2>/dev/null; then
      echo "  aiagentdb               → localhost:$AIAGENT_LOCAL_PORT  [실행중 PID=$PID_AIAGENT]"
    else
      echo "  aiagentdb               → localhost:$AIAGENT_LOCAL_PORT  [종료됨]"
    fi
  else
    echo "  실행중인 터널 없음"
  fi

  echo ""
  echo "=== 연결 확인 ==="
  nc -z localhost "$SAFETY_LOCAL_PORT" 2>/dev/null \
    && echo "  :$SAFETY_LOCAL_PORT  [도달 가능]" \
    || echo "  :$SAFETY_LOCAL_PORT  [도달 불가]"
  nc -z localhost "$AIAGENT_LOCAL_PORT" 2>/dev/null \
    && echo "  :$AIAGENT_LOCAL_PORT  [도달 가능]" \
    || echo "  :$AIAGENT_LOCAL_PORT  [도달 불가]"

  echo ""
  echo "=== K8s secret 현재값 ==="
  if kubectl cluster-info --context "$K8S_CONTEXT" >/dev/null 2>&1; then
    for key in WORKSHOP_DATABASE_URL TANGO_DATABASE_URL DOCULOG_DATABASE_URL DOCULOG_DB_PASSWORD; do
      val=$(kubectl get secret "$K8S_SECRET" -n "$K8S_NS" --context "$K8S_CONTEXT" \
        -o jsonpath="{.data.${key}}" 2>/dev/null | base64 -d 2>/dev/null || true)
      if [ -n "$val" ]; then
        echo "  $key = ${val:0:60}..."
      else
        echo "  $key = (비어있음)"
      fi
    done
  else
    echo "  Docker Desktop K8s 미실행"
  fi
}

# ============================================================
# 진입점
# ============================================================
case "${1:-}" in
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  status) cmd_status ;;
  patch)
    # 터널이 이미 실행 중인 상태에서 K8s secret만 다시 패치
    # (local-dev-up.sh 실행 후 platform 네임스페이스가 생긴 경우 사용)
    log "자격증명 소스 탐지..."
    load_credentials
    do_patch "$WORKSHOP_URL" "$TANGO_URL" "$DOCULOG_URL" "$DOCULOG_PW"
    echo ""
    echo "✅ 패치 완료. auth-gateway Pod 재시작:"
    echo "   kubectl rollout restart deployment/auth-gateway -n $K8S_NS --context $K8S_CONTEXT"
    ;;
  *)
    echo "Usage: $0 {start|stop|status|patch}"
    echo ""
    echo "  start   — SSM 터널 시작 + 로컬 K8s secret 패치"
    echo "  stop    — 터널 종료 + K8s secret 초기화"
    echo "  status  — 터널/연결/secret 상태 확인"
    echo "  patch   — 터널 유지한 채 K8s secret만 재패치 (local-dev-up.sh 후 사용)"
    exit 1
    ;;
esac
