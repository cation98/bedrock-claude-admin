#!/bin/bash
# =============================================================================
# 로컬 개발환경 전체 구동 스크립트
# Docker Desktop K8s에서 전체 시스템을 로컬에서 구동합니다.
#
# 사용법: ./scripts/local-dev-up.sh
# 종료:   ./scripts/local-dev-down.sh
# =============================================================================

set -euo pipefail

CONTEXT="docker-desktop"
K="kubectl --context=${CONTEXT}"
LOCAL_DEV_DIR="$(cd "$(dirname "$0")/../infra/local-dev" && pwd)"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=============================================="
echo "  Otto AI 로컬 개발환경 구동"
echo "=============================================="
echo ""

# --- 1. Docker Desktop K8s 확인 ---
echo "[1/8] Docker Desktop K8s 확인..."
if ! $K get nodes &>/dev/null; then
    echo "❌ Docker Desktop K8s에 연결할 수 없습니다."
    echo "   Docker Desktop → Settings → Kubernetes → Enable Kubernetes 확인"
    exit 1
fi
echo "  ✅ $(${K} get nodes --no-headers | awk '{print $1, $2, $5}')"
echo ""

# --- 2. 이미지 빌드 ---
echo "[2/8] Docker 이미지 빌드..."
echo "  auth-gateway..."
docker build -q -t bedrock-claude/auth-gateway:local "${PROJECT_DIR}/auth-gateway/" > /dev/null
echo "  ✅ bedrock-claude/auth-gateway:local"

echo "  container-image..."
docker build -q -t bedrock-claude/claude-code-terminal:local "${PROJECT_DIR}/container-image/" > /dev/null
echo "  ✅ bedrock-claude/claude-code-terminal:local"

echo "  usage-worker..."
docker build -q -t bedrock-claude/usage-worker:local "${PROJECT_DIR}/usage-worker/" > /dev/null
echo "  ✅ bedrock-claude/usage-worker:local"

# Docker Desktop 4.34+는 K8s를 kind 기반으로 운영 — 호스트 Docker 이미지가
# kind 노드(desktop-control-plane)의 containerd로 자동 동기화되지 않아
# imagePullPolicy: Never가 ErrImageNeverPull로 실패함.
# 'docker save | ctr import'로 k8s.io 네임스페이스에 명시적으로 적재.
if docker ps --format '{{.Names}}' | grep -q '^desktop-control-plane$'; then
    echo "  kind 노드에 이미지 적재 중 (Docker Desktop containerd k8s.io)..."
    for img in bedrock-claude/auth-gateway:local bedrock-claude/claude-code-terminal:local; do
        docker save "$img" \
          | docker exec -i desktop-control-plane ctr -n k8s.io images import - > /dev/null 2>&1
        echo "    ✅ $img → desktop-control-plane:k8s.io"
    done
fi
echo ""

# --- 3. K8s 리소스 생성 ---
echo "[3/8] 네임스페이스..."
$K apply -f "${LOCAL_DEV_DIR}/00-namespaces.yaml" > /dev/null
echo "  ✅ platform, claude-sessions"

echo "[4/8] PostgreSQL + Redis..."
$K apply -f "${LOCAL_DEV_DIR}/01-postgresql.yaml" > /dev/null
$K apply -f "${LOCAL_DEV_DIR}/02-redis.yaml" > /dev/null
echo "  ✅ local-db, redis"

echo "[5/8] Secrets + RBAC..."
$K apply -f "${LOCAL_DEV_DIR}/02-secrets.yaml" > /dev/null
$K apply -f "${LOCAL_DEV_DIR}/03-service-account.yaml" > /dev/null
echo "  ✅ auth-gateway-secrets, platform-admin-sa"

echo "[6/8] Auth Gateway + Usage Worker..."
$K apply -f "${LOCAL_DEV_DIR}/04-auth-gateway.yaml" > /dev/null
$K apply -f "${LOCAL_DEV_DIR}/05-usage-worker.yaml" > /dev/null
echo "  대기 중..."
$K wait --for=condition=Ready pod -l app=auth-gateway -n platform --timeout=90s > /dev/null 2>&1 || true
echo "  ✅ auth-gateway deployment + service"
echo "  ✅ usage-worker deployment"

# --- 3.5. DB Seed — 테스트 사용자 생성 ---
echo "  DB seed (TEST001)..."
# auth-gateway가 create_all로 테이블 생성한 후 seed 실행
DB_POD=$($K get pod -l app=local-db -n platform -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -n "$DB_POD" ]; then
    $K exec "$DB_POD" -n platform -- psql -U bedrock_admin -d bedrock_platform -c "
        INSERT INTO users (username, name, role, is_active, is_approved, pod_ttl, storage_retention, can_deploy_custom_auth, approved_at, created_at, updated_at)
        VALUES ('TEST001', '테스트사용자', 'user', true, true, '4h', '30d', false, NOW(), NOW(), NOW())
        ON CONFLICT (username) DO NOTHING;
    " > /dev/null 2>&1
    echo "  ✅ TEST001 사용자 seed 완료"
else
    echo "  ⚠️  DB Pod을 찾을 수 없음 — 수동 seed 필요"
fi

# --- 4. Ingress Controller ---
echo "[7/8] Nginx Ingress Controller..."
if $K get pods -n ingress-nginx --no-headers 2>/dev/null | grep -q "Running"; then
    echo "  ✅ 이미 설치됨"
else
    echo "  설치 중 (최초 1회, ~30초)..."
    $K apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.0/deploy/static/provider/cloud/deploy.yaml > /dev/null 2>&1
    echo "  대기 중..."
    $K wait --namespace ingress-nginx --for=condition=ready pod \
        --selector=app.kubernetes.io/component=controller --timeout=120s > /dev/null 2>&1 || true
    echo "  ✅ 설치 완료"
fi
$K apply -f "${LOCAL_DEV_DIR}/06-ingress.yaml" > /dev/null
echo "  ✅ Ingress 규칙 적용"

# --- 5. OnlyOffice + 사용자 Pod ---
echo "[8/8] OnlyOffice + 테스트 사용자 Pod..."
$K apply -f "${LOCAL_DEV_DIR}/07-onlyoffice.yaml" > /dev/null

# AWS 자격증명 Secret
if [ -f "$HOME/.aws/credentials" ]; then
    $K create secret generic aws-credentials \
        --from-file=credentials="$HOME/.aws/credentials" \
        --from-file=config="$HOME/.aws/config" \
        -n claude-sessions --dry-run=client -o yaml | $K apply -f - > /dev/null 2>&1
    echo "  ✅ AWS 자격증명 등록"
else
    echo "  ⚠️  ~/.aws/credentials 없음 — Bedrock 호출 불가"
fi

$K apply -f "${LOCAL_DEV_DIR}/08-test-user-pod.yaml" > /dev/null
echo "  ✅ claude-terminal-test001"

# --- 6. 상태 확인 ---
echo ""
echo "=============================================="
echo "  구동 완료! 상태 확인 중..."
echo "=============================================="
echo ""

echo "=== platform ==="
$K get pods -n platform
echo ""
echo "=== claude-sessions ==="
$K get pods -n claude-sessions
echo ""

echo "=============================================="
echo "  접속 정보"
echo "=============================================="
echo ""
echo "  로그인:    http://localhost/"
echo "  ID/PW:     TEST001 / test2026"
echo ""
echo "  Hub:       http://localhost/hub/claude-terminal-test001/"
echo "  터미널:    http://localhost/terminal/claude-terminal-test001/"
echo "  파일관리:  http://localhost/files/claude-terminal-test001/"
echo ""
echo "  종료: ./scripts/local-dev-down.sh"
echo "=============================================="
