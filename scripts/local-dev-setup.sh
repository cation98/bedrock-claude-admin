#!/bin/bash
# =============================================================================
# 로컬 개발환경 구축 스크립트 (Mac Studio + Docker Desktop K8s)
#
# 사용법:
#   ./scripts/local-dev-setup.sh          # 전체 구축
#   ./scripts/local-dev-setup.sh teardown # 전체 삭제
#   ./scripts/local-dev-setup.sh status   # 상태 확인
# =============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_DEV="$PROJECT_ROOT/infra/local-dev"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[LOCAL-DEV]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Teardown ──
if [ "${1:-}" = "teardown" ]; then
    log "로컬 개발환경 삭제 중..."
    kubectl config use-context docker-desktop 2>/dev/null
    kubectl delete -f "$LOCAL_DEV/" --ignore-not-found 2>/dev/null || true
    kubectl delete namespace claude-sessions --ignore-not-found 2>/dev/null || true
    log "삭제 완료. EKS로 전환: kubectl config use-context arn:aws:eks:..."
    exit 0
fi

# ── Status ──
if [ "${1:-}" = "status" ]; then
    kubectl config use-context docker-desktop 2>/dev/null
    echo ""
    log "=== 노드 ==="
    kubectl get nodes -o wide 2>&1
    echo ""
    log "=== Pod (전체) ==="
    kubectl get pods -A 2>&1
    echo ""
    log "=== Service ==="
    kubectl get svc -A 2>&1 | grep -v "kube-system\|default"
    echo ""
    log "=== 접속 URL ==="
    AUTH_PORT=$(kubectl get svc auth-gateway -n platform -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "미배포")
    echo "  auth-gateway: http://localhost:${AUTH_PORT}"
    echo "  admin-dashboard: http://localhost:3000 (npm run dev)"
    exit 0
fi

# ── Setup ──
log "로컬 개발환경 구축 시작..."

# 1. Context 확인
log "1/6. Docker Desktop K8s context 전환..."
if ! kubectl config use-context docker-desktop 2>/dev/null; then
    err "docker-desktop context가 없습니다. Docker Desktop > Settings > Kubernetes > Enable 확인"
    exit 1
fi
kubectl get nodes -o wide

# 2. 네임스페이스 + DB + Secret + RBAC
log "2/6. 네임스페이스, DB, Secret, RBAC 생성..."
kubectl apply -f "$LOCAL_DEV/00-namespaces.yaml"
kubectl apply -f "$LOCAL_DEV/01-postgresql.yaml"
kubectl apply -f "$LOCAL_DEV/02-secrets.yaml"
kubectl apply -f "$LOCAL_DEV/03-service-account.yaml"

# 3. PostgreSQL 준비 대기
log "3/6. PostgreSQL 준비 대기..."
kubectl wait --for=condition=Ready pod -l app=local-db -n platform --timeout=60s

# 4. auth-gateway 이미지 로컬 빌드
log "4/6. auth-gateway 이미지 빌드 (로컬)..."
docker build -t bedrock-claude/auth-gateway:local "$PROJECT_ROOT/auth-gateway"

# 5. auth-gateway 배포
log "5/6. auth-gateway 배포..."
kubectl apply -f "$LOCAL_DEV/04-auth-gateway.yaml"
kubectl wait --for=condition=Ready pod -l app=auth-gateway -n platform --timeout=90s

# 6. 결과 확인
log "6/6. 환경 확인..."
echo ""
kubectl get pods -A | grep -v "kube-system"
echo ""

AUTH_PORT=$(kubectl get svc auth-gateway -n platform -o jsonpath='{.spec.ports[0].nodePort}')
echo ""
log "============================================"
log "  로컬 개발환경 구축 완료!"
log "============================================"
log ""
log "  auth-gateway: http://localhost:${AUTH_PORT}"
log "  admin-dashboard: cd admin-dashboard && npm run dev"
log ""
log "  EKS 전환: kubectl config use-context arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks"
log "  로컬 전환: kubectl config use-context docker-desktop"
log "  상태 확인: ./scripts/local-dev-setup.sh status"
log "  환경 삭제: ./scripts/local-dev-setup.sh teardown"
log ""
