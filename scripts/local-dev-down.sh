#!/bin/bash
# =============================================================================
# 로컬 개발환경 종료 스크립트
# 사용법: ./scripts/local-dev-down.sh
# =============================================================================

set -euo pipefail

CONTEXT="docker-desktop"
K="kubectl --context=${CONTEXT}"
LOCAL_DEV_DIR="$(cd "$(dirname "$0")/../infra/local-dev" && pwd)"

echo "=============================================="
echo "  Otto AI 로컬 개발환경 종료"
echo "=============================================="
echo ""

echo "사용자 Pod 삭제..."
$K delete -f "${LOCAL_DEV_DIR}/08-test-user-pod.yaml" --ignore-not-found > /dev/null 2>&1
echo "  ✅ claude-terminal-test001"

echo "OnlyOffice 삭제..."
$K delete -f "${LOCAL_DEV_DIR}/07-onlyoffice.yaml" --ignore-not-found > /dev/null 2>&1
echo "  ✅ onlyoffice"

echo "Ingress 규칙 삭제..."
$K delete -f "${LOCAL_DEV_DIR}/06-ingress.yaml" --ignore-not-found > /dev/null 2>&1
echo "  ✅ ingress"

echo "Auth Gateway 삭제..."
$K delete -f "${LOCAL_DEV_DIR}/04-auth-gateway.yaml" --ignore-not-found > /dev/null 2>&1
echo "  ✅ auth-gateway"

echo "Redis 삭제..."
$K delete -f "${LOCAL_DEV_DIR}/02-redis.yaml" --ignore-not-found > /dev/null 2>&1
echo "  ✅ redis"

echo "PostgreSQL 삭제..."
$K delete -f "${LOCAL_DEV_DIR}/01-postgresql.yaml" --ignore-not-found > /dev/null 2>&1
echo "  ✅ local-db"

echo "AWS 자격증명 Secret 삭제..."
$K delete secret aws-credentials -n claude-sessions --ignore-not-found > /dev/null 2>&1
echo "  ✅ aws-credentials"

echo ""
echo "=============================================="
echo "  종료 완료"
echo "  네임스페이스, Secrets, RBAC, Ingress Controller는 유지됩니다."
echo "  완전 삭제: kubectl --context=docker-desktop delete ns platform claude-sessions"
echo "=============================================="
