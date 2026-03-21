#!/bin/bash
# =============================================================================
# 통합 테스트 스크립트
#
# 두 가지 모드로 실행:
#   ./scripts/test-integration.sh local    — 로컬 Docker 테스트
#   ./scripts/test-integration.sh cluster  — EKS 클러스터 테스트 (terraform apply 후)
# =============================================================================

set -euo pipefail

MODE="${1:-local}"
PASS=0
FAIL=0
SKIP=0

# 색상
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}  [PASS]${NC} $1"; ((PASS++)); }
fail() { echo -e "${RED}  [FAIL]${NC} $1 — $2"; ((FAIL++)); }
skip() { echo -e "${YELLOW}  [SKIP]${NC} $1 — $2"; ((SKIP++)); }

echo "============================================"
echo "  Integration Test — Mode: ${MODE}"
echo "============================================"
echo ""

# ==================== LOCAL MODE ====================
if [ "$MODE" = "local" ]; then

    echo "--- Container Image Tests ---"

    # Test 1: Docker 이미지 빌드
    if docker image inspect claude-code-terminal:latest >/dev/null 2>&1; then
        pass "Container image exists"
    else
        echo "  Building image..."
        if docker build -t claude-code-terminal container-image/ >/dev/null 2>&1; then
            pass "Container image built"
        else
            fail "Container image build" "docker build failed"
        fi
    fi

    # Test 2: 컨테이너 실행 + ttyd 포트
    docker run -d --name test-terminal -p 17681:7681 claude-code-terminal >/dev/null 2>&1 || true
    sleep 3

    if docker exec test-terminal claude --version >/dev/null 2>&1; then
        pass "Claude Code CLI available in container"
    else
        fail "Claude Code CLI" "not found in container"
    fi

    if docker exec test-terminal psql --version >/dev/null 2>&1; then
        pass "psql client available in container"
    else
        fail "psql client" "not found in container"
    fi

    if docker exec test-terminal aws --version >/dev/null 2>&1; then
        pass "AWS CLI available in container"
    else
        fail "AWS CLI" "not found in container"
    fi

    docker stop test-terminal >/dev/null 2>&1 && docker rm test-terminal >/dev/null 2>&1

    echo ""
    echo "--- Auth Gateway Tests ---"

    cd auth-gateway
    if [ ! -f .env ]; then
        cp .env.example .env
    fi

    docker compose up -d --build >/dev/null 2>&1
    sleep 8  # DB healthcheck + API startup

    # Test 3: Health endpoint
    STATUS=$(docker compose exec -T api python -c "
import httpx
r = httpx.get('http://localhost:8000/health')
print(r.status_code)
" 2>/dev/null | tr -d '[:space:]')

    if [ "$STATUS" = "200" ]; then
        pass "Auth Gateway health endpoint"
    else
        fail "Auth Gateway health" "status=$STATUS"
    fi

    # Test 4: API endpoints registered
    ENDPOINTS=$(docker compose exec -T api python -c "
import httpx
r = httpx.get('http://localhost:8000/openapi.json')
print(len(r.json().get('paths', {})))
" 2>/dev/null | tr -d '[:space:]')

    if [ "$ENDPOINTS" -ge 6 ] 2>/dev/null; then
        pass "API endpoints registered ($ENDPOINTS endpoints)"
    else
        fail "API endpoints" "found $ENDPOINTS"
    fi

    # Test 5: JWT 인증 흐름
    AUTH_TEST=$(docker compose exec -T api python -c "
import httpx
from app.core.security import create_access_token
token = create_access_token({'sub': 'test', 'user_id': 1, 'role': 'user'})
r = httpx.get('http://localhost:8000/api/v1/sessions/', headers={'Authorization': f'Bearer {token}'})
print(r.status_code)
" 2>/dev/null | tr -d '[:space:]')

    if [ "$AUTH_TEST" = "200" ]; then
        pass "JWT authentication flow"
    else
        fail "JWT authentication" "status=$AUTH_TEST"
    fi

    # Test 6: 비인증 요청 거부
    UNAUTH_TEST=$(docker compose exec -T api python -c "
import httpx
r = httpx.get('http://localhost:8000/api/v1/auth/me')
print(r.status_code)
" 2>/dev/null | tr -d '[:space:]')

    if [ "$UNAUTH_TEST" = "403" ]; then
        pass "Unauthenticated request rejected (403)"
    else
        fail "Unauthenticated rejection" "status=$UNAUTH_TEST"
    fi

    # Test 7: 권한 제어 (non-admin → admin endpoint)
    RBAC_TEST=$(docker compose exec -T api python -c "
import httpx
from app.core.security import create_access_token
token = create_access_token({'sub': 'user', 'user_id': 99, 'role': 'user'})
r = httpx.get('http://localhost:8000/api/v1/sessions/active', headers={'Authorization': f'Bearer {token}'})
print(r.status_code)
" 2>/dev/null | tr -d '[:space:]')

    if [ "$RBAC_TEST" = "403" ]; then
        pass "RBAC: non-admin blocked from admin endpoint"
    else
        fail "RBAC" "status=$RBAC_TEST"
    fi

    docker compose down >/dev/null 2>&1
    cd ..

    skip "K8s Pod creation" "requires EKS cluster (run with 'cluster' mode)"
    skip "Bedrock API access" "requires AWS credentials + Bedrock model access"
    skip "SSO integration" "requires sso.skons.net connectivity"

fi

# ==================== CLUSTER MODE ====================
if [ "$MODE" = "cluster" ]; then

    echo "--- Prerequisites ---"

    # AWS 인증 확인
    if aws sts get-caller-identity >/dev/null 2>&1; then
        ACCOUNT=$(aws sts get-caller-identity --query 'Account' --output text)
        pass "AWS credentials valid (account: $ACCOUNT)"
    else
        fail "AWS credentials" "not configured"
        echo "Run: aws sso login"
        exit 1
    fi

    # kubectl 연결 확인
    if kubectl get nodes >/dev/null 2>&1; then
        NODE_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')
        pass "EKS cluster connected ($NODE_COUNT nodes)"
    else
        fail "EKS cluster" "kubectl cannot connect"
        echo "Run: aws eks update-kubeconfig --name bedrock-claude-eks --region ap-northeast-2"
        exit 1
    fi

    echo ""
    echo "--- Namespace & Resources ---"

    # Namespace 존재 확인
    if kubectl get namespace claude-sessions >/dev/null 2>&1; then
        pass "Namespace 'claude-sessions' exists"
    else
        fail "Namespace" "claude-sessions not found"
    fi

    # ServiceAccount 확인
    if kubectl get serviceaccount claude-terminal-sa -n claude-sessions >/dev/null 2>&1; then
        pass "ServiceAccount 'claude-terminal-sa' exists"
    else
        fail "ServiceAccount" "not found"
    fi

    echo ""
    echo "--- Pod Creation Test ---"

    # 테스트 Pod 생성
    POD_NAME="integration-test-pod"
    ECR_URL=$(cd infra/terraform && terraform output -raw ecr_repository_url 2>/dev/null)

    if [ -z "$ECR_URL" ]; then
        fail "ECR URL" "terraform output not available"
    else
        # Pod manifest 생성
        cat <<YAML | kubectl apply -f - 2>/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${POD_NAME}
  namespace: claude-sessions
  labels:
    app: claude-terminal
    user: integration-test
spec:
  serviceAccountName: claude-terminal-sa
  restartPolicy: Never
  activeDeadlineSeconds: 300
  containers:
    - name: terminal
      image: ${ECR_URL}:latest
      ports:
        - containerPort: 7681
      env:
        - name: CLAUDE_CODE_USE_BEDROCK
          value: "1"
        - name: AWS_REGION
          value: "us-east-1"
      resources:
        requests:
          cpu: "250m"
          memory: "256Mi"
        limits:
          cpu: "500m"
          memory: "512Mi"
YAML

        echo "  Waiting for pod to start (max 60s)..."
        if kubectl wait --for=condition=Ready pod/${POD_NAME} -n claude-sessions --timeout=60s >/dev/null 2>&1; then
            pass "Test pod is Running"

            # Pod 내부에서 Claude Code 확인
            CLAUDE_VER=$(kubectl exec ${POD_NAME} -n claude-sessions -- claude --version 2>/dev/null || echo "failed")
            if [[ "$CLAUDE_VER" == *"Claude Code"* ]]; then
                pass "Claude Code CLI works in pod ($CLAUDE_VER)"
            else
                fail "Claude Code in pod" "$CLAUDE_VER"
            fi

            # IRSA 확인 (AWS 자격증명 자동 주입)
            AWS_ID=$(kubectl exec ${POD_NAME} -n claude-sessions -- aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null || echo "failed")
            if [[ "$AWS_ID" == *"bedrock"* ]]; then
                pass "IRSA: Bedrock role assumed ($AWS_ID)"
            else
                fail "IRSA" "unexpected identity: $AWS_ID"
            fi

            # Bedrock 모델 접근 확인
            MODEL_CHECK=$(kubectl exec ${POD_NAME} -n claude-sessions -- aws bedrock list-foundation-models --region us-east-1 --by-provider anthropic --query 'modelSummaries[0].modelId' --output text 2>/dev/null || echo "failed")
            if [[ "$MODEL_CHECK" == *"claude"* ]]; then
                pass "Bedrock model access works ($MODEL_CHECK)"
            else
                fail "Bedrock access" "$MODEL_CHECK"
            fi
        else
            fail "Test pod" "did not become Ready within 60s"
            kubectl describe pod ${POD_NAME} -n claude-sessions 2>/dev/null | tail -20
        fi

        # 테스트 Pod 정리
        kubectl delete pod ${POD_NAME} -n claude-sessions --grace-period=5 >/dev/null 2>&1 || true
        pass "Test pod cleaned up"
    fi

    echo ""
    echo "--- Network Policy ---"

    if kubectl get networkpolicy isolate-terminal-pods -n claude-sessions >/dev/null 2>&1; then
        pass "NetworkPolicy exists"
    else
        skip "NetworkPolicy" "not deployed yet"
    fi
fi

# ==================== Summary ====================
echo ""
echo "============================================"
echo "  Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped"
echo "============================================"

if [ $FAIL -gt 0 ]; then
    exit 1
fi
