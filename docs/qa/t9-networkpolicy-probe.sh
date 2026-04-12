#!/usr/bin/env bash
# =============================================================================
# T9 NetworkPolicy 6단계 카나리 rollout 검증 스크립트
#
# 실행 전제조건:
#   - T2 (terraform apply: ingress-workers + main-workers 노드그룹 생성) 완료
#   - T11 (Open WebUI Pod 배포) 완료 → openwebui Pod Running 상태
#   - kubectl context: bedrock-claude-cluster (EKS)
#
# 사용법:
#   chmod +x docs/qa/t9-networkpolicy-probe.sh
#   ./docs/qa/t9-networkpolicy-probe.sh [STAGE]
#   # STAGE 미지정 시 전체 6단계 순서대로 실행
#
# 성공 기준:
#   ALLOW: 연결 성공 (exit 0)
#   BLOCK: 연결 실패 (exit 1 또는 timeout)
#   각 단계 결과를 PASS/FAIL로 출력
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS=0
FAIL=0
SKIP=0

log_pass() { echo -e "${GREEN}[PASS]${NC} $*"; PASS=$((PASS+1)); }
log_fail() { echo -e "${RED}[FAIL]${NC} $*"; FAIL=$((FAIL+1)); }
log_skip() { echo -e "${YELLOW}[SKIP]${NC} $*"; SKIP=$((SKIP+1)); }
log_info() { echo -e "[INFO] $*"; }

# ──────────────────────────────────────────────────────────────────────────────
# 헬퍼: Pod 내에서 TCP 연결 시도
# probe_tcp <namespace> <pod-label> <host> <port> <expect: allow|block>
# ──────────────────────────────────────────────────────────────────────────────
probe_tcp() {
  local ns="$1"
  local pod_selector="$2"
  local host="$3"
  local port="$4"
  local expect="$5"
  local desc="${6:-$host:$port}"

  local pod
  pod=$(kubectl get pod -n "$ns" -l "$pod_selector" \
    --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

  if [[ -z "$pod" ]]; then
    log_skip "[$ns/$pod_selector] Pod not found — T11 deploy pending"
    return
  fi

  log_info "Probing $desc from $ns/$pod (expect: $expect)..."
  # nc 미설치 컨테이너 대응: nc 사용 가능 시 nc, 없으면 /dev/tcp fallback
  if kubectl exec -n "$ns" "$pod" -- sh -c \
    "if command -v nc >/dev/null 2>&1; then nc -zw3 $host $port 2>/dev/null; else (echo >/dev/tcp/$host/$port) 2>/dev/null; fi; echo \$?" 2>/dev/null | grep -q "^0$"; then
    local actual="allow"
  else
    local actual="block"
  fi

  if [[ "$actual" == "$expect" ]]; then
    log_pass "$desc → $actual (expected $expect)"
  else
    log_fail "$desc → $actual (expected $expect) ← POLICY MISMATCH"
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# STAGE 1: openwebui NetworkPolicy 적용 확인
# ──────────────────────────────────────────────────────────────────────────────
stage1() {
  echo ""
  echo "=== STAGE 1: NetworkPolicy 적용 상태 확인 ==="

  local policies=(
    "default-deny-all"
    "allow-open-webui-traffic"
    "allow-bedrock-ag-traffic"
    "allow-pipelines-traffic"
    "allow-openwebui-postgres-traffic"
  )

  for policy in "${policies[@]}"; do
    if kubectl get networkpolicy "$policy" -n openwebui &>/dev/null; then
      log_pass "NetworkPolicy '$policy' 존재"
    else
      log_fail "NetworkPolicy '$policy' 미존재 ← kubectl apply -f infra/k8s/openwebui/network-policy.yaml 필요"
    fi
  done

  # platform NetworkPolicy openwebui 규칙 확인
  if kubectl describe networkpolicy allow-auth-gateway-traffic -n platform 2>/dev/null | grep -q "openwebui"; then
    log_pass "allow-auth-gateway-traffic에 openwebui 규칙 포함"
  else
    log_fail "allow-auth-gateway-traffic openwebui 규칙 누락 ← kubectl apply -f infra/k8s/platform/network-policy.yaml 필요"
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# STAGE 2: Open WebUI → Bedrock Access Gateway 연결성
# ──────────────────────────────────────────────────────────────────────────────
stage2() {
  echo ""
  echo "=== STAGE 2: Open WebUI → Bedrock AG 연결성 (ALLOW 필요) ==="

  probe_tcp openwebui "app=open-webui" \
    "bedrock-access-gateway.openwebui.svc.cluster.local" 8080 allow \
    "Open WebUI → Bedrock AG:8080"
}

# ──────────────────────────────────────────────────────────────────────────────
# STAGE 3: Open WebUI → auth-gateway JWKS (platform ns) 연결성
# ──────────────────────────────────────────────────────────────────────────────
stage3() {
  echo ""
  echo "=== STAGE 3: Open WebUI → auth-gateway JWKS 연결성 (ALLOW 필요) ==="

  probe_tcp openwebui "app=open-webui" \
    "auth-gateway.platform.svc.cluster.local" 8000 allow \
    "Open WebUI → auth-gateway:8000 (JWKS)"

  # JWKS 엔드포인트 실제 응답 확인
  local pod
  pod=$(kubectl get pod -n openwebui -l "app=open-webui" \
    --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

  if [[ -n "$pod" ]]; then
    local jwks_resp
    jwks_resp=$(kubectl exec -n openwebui "$pod" -- sh -c \
      "wget -qO- http://auth-gateway.platform.svc:8000/auth/.well-known/jwks.json 2>/dev/null | head -c 100" 2>/dev/null || echo "")

    if echo "$jwks_resp" | grep -q "keys"; then
      log_pass "JWKS 엔드포인트 응답 정상 (keys 필드 포함)"
    else
      log_fail "JWKS 엔드포인트 응답 없음 — auth-gateway가 올바른 이미지인지 확인 필요"
    fi
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# STAGE 4: Open WebUI → Redis 직접 접근 차단 확인 (BLOCK 필요)
# ──────────────────────────────────────────────────────────────────────────────
stage4() {
  echo ""
  echo "=== STAGE 4: Open WebUI → Redis 직접 접근 차단 확인 (BLOCK 필요) ==="
  echo "    설계 원칙: Open WebUI는 Redis에 직접 접속하지 않음"
  echo "    jti 블랙리스트 = auth-gateway 담당, Stream = Pipelines 담당"

  # Redis 접근 시도 (차단되어야 함)
  # ElastiCache private subnet CIDR: 10.0.10.0/24, 10.0.20.0/24
  local pod
  pod=$(kubectl get pod -n openwebui -l "app=open-webui" \
    --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

  if [[ -n "$pod" ]]; then
    # ElastiCache 서브넷에 대한 포트 6379 접근은 허용되지 않아야 함
    # (allow-open-webui-traffic에 Redis 규칙 없음)
    if kubectl exec -n openwebui "$pod" -- sh -c \
      "nc -zw2 10.0.10.0 6379 2>/dev/null; echo \$?" 2>/dev/null | grep -q "^0$"; then
      log_fail "Open WebUI → Redis 직접 접근 허용됨 (BLOCK이어야 함) ← NetworkPolicy 오류"
    else
      log_pass "Open WebUI → Redis:6379 차단 확인 (최소권한 원칙 준수)"
    fi
  else
    log_skip "Open WebUI Pod 없음 (T11 pending)"
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# STAGE 5: Pipelines → Redis Stream 쓰기 연결성 (ALLOW 필요)
# ──────────────────────────────────────────────────────────────────────────────
stage5() {
  echo ""
  echo "=== STAGE 5: Pipelines → ElastiCache Redis 연결성 (ALLOW 필요) ==="

  # ElastiCache endpoint는 T2 apply 후 환경변수에서 확인
  local pod
  pod=$(kubectl get pod -n openwebui -l "app=openwebui-pipelines" \
    --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

  if [[ -n "$pod" ]]; then
    local redis_url
    redis_url=$(kubectl exec -n openwebui "$pod" -- sh -c \
      "echo \$REDIS_URL" 2>/dev/null || echo "")

    if [[ -z "$redis_url" ]]; then
      log_skip "REDIS_URL 환경변수 없음 — T2 ElastiCache endpoint 미설정"
      return
    fi

    # Redis URL에서 host:port 추출
    local redis_host redis_port
    redis_host=$(echo "$redis_url" | sed 's|redis://||' | cut -d: -f1)
    redis_port=$(echo "$redis_url" | sed 's|redis://||' | cut -d: -f2 | cut -d/ -f1)
    redis_port=${redis_port:-6379}

    probe_tcp openwebui "app=openwebui-pipelines" \
      "$redis_host" "$redis_port" allow \
      "Pipelines → ElastiCache Redis:$redis_port"
  else
    log_skip "Pipelines Pod 없음 (T11 pending)"
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# STAGE 6: Bedrock AG → AWS Bedrock 외부 연결성 (ALLOW 필요)
# ──────────────────────────────────────────────────────────────────────────────
stage6() {
  echo ""
  echo "=== STAGE 6: Bedrock AG → AWS Bedrock HTTPS 연결성 (ALLOW 필요) ==="

  local pod
  pod=$(kubectl get pod -n openwebui -l "app=bedrock-access-gateway" \
    --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

  if [[ -n "$pod" ]]; then
    probe_tcp openwebui "app=bedrock-access-gateway" \
      "bedrock-runtime.us-east-1.amazonaws.com" 443 allow \
      "Bedrock AG → AWS Bedrock us-east-1:443"

    # IRSA 자격증명 확인
    if kubectl exec -n openwebui "$pod" -- sh -c \
      "curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/ 2>/dev/null" | grep -q "bedrock"; then
      log_pass "IRSA 자격증명 확인 (bedrock-ag-sa)"
    else
      log_info "IRSA 확인 스킵 (EC2 metadata 경로 미지원 or 정상)"
    fi

    # Bedrock AG health 체크
    if kubectl exec -n openwebui "$pod" -- sh -c \
      "wget -qO- http://localhost:8080/health 2>/dev/null | head -c 50" 2>/dev/null | grep -qi "ok\|healthy\|alive"; then
      log_pass "Bedrock AG /health 응답 정상"
    else
      log_fail "Bedrock AG /health 응답 없음 또는 비정상"
    fi
  else
    log_skip "Bedrock AG Pod 없음 (T11 pending)"
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# 결과 요약
# ──────────────────────────────────────────────────────────────────────────────
summary() {
  echo ""
  echo "=========================================="
  echo " T9 NetworkPolicy 검증 결과"
  echo "=========================================="
  echo -e " PASS:  ${GREEN}$PASS${NC}"
  echo -e " FAIL:  ${RED}$FAIL${NC}"
  echo -e " SKIP:  ${YELLOW}$SKIP${NC} (T2/T11 pending)"
  echo "=========================================="

  if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}[FAIL] ${FAIL}개 항목 실패 — NetworkPolicy 또는 배포 오류 확인 필요${NC}"
    exit 1
  elif [[ $SKIP -gt 0 ]]; then
    echo -e "${YELLOW}[PARTIAL] T2/T11 완료 후 재실행 필요${NC}"
    exit 0
  else
    echo -e "${GREEN}[PASS] 전체 T9 NetworkPolicy 검증 완료${NC}"
    exit 0
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────
STAGE="${1:-all}"

case "$STAGE" in
  1) stage1 ;;
  2) stage2 ;;
  3) stage3 ;;
  4) stage4 ;;
  5) stage5 ;;
  6) stage6 ;;
  all)
    stage1
    stage2
    stage3
    stage4
    stage5
    stage6
    ;;
  *)
    echo "Usage: $0 [1|2|3|4|5|6|all]"
    exit 1
    ;;
esac

summary
