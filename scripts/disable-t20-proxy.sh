#!/usr/bin/env bash
# disable-t20-proxy.sh — T20 proxy를 비활성화하거나 활성화한다.
#
# 사용법:
#   ./scripts/disable-t20-proxy.sh              # dry-run (변경 없음, 계획만 출력)
#   ./scripts/disable-t20-proxy.sh --apply      # 실제 적용
#   ./scripts/disable-t20-proxy.sh --apply --enable  # 재활성화

set -euo pipefail

APPLY=false
ENABLE=false
NS=platform
DEPLOYMENT=auth-gateway
ENV_VAR=T20_PROXY_ENABLED
TARGET_VALUE="false"

for arg in "$@"; do
  case $arg in
    --apply)  APPLY=true ;;
    --enable) ENABLE=true; TARGET_VALUE="true" ;;
  esac
done

action=$([ "$ENABLE" = "true" ] && echo "활성화" || echo "비활성화")

echo "=== T20 Proxy ${action} ==="
echo "  Namespace:  $NS"
echo "  Deployment: $DEPLOYMENT"
echo "  Env:        ${ENV_VAR}=${TARGET_VALUE}"
echo ""

CURRENT=$(kubectl get deployment "$DEPLOYMENT" -n "$NS" \
  -o jsonpath="{.spec.template.spec.containers[0].env[?(@.name=='${ENV_VAR}')].value}" 2>/dev/null || echo "unset")

echo "  현재 값: ${CURRENT:-unset}"
echo "  변경 후: ${TARGET_VALUE}"
echo ""

if [ "$CURRENT" = "$TARGET_VALUE" ]; then
  echo "✅ 이미 원하는 상태입니다. 변경 없음."
  exit 0
fi

if [ "$APPLY" = "false" ]; then
  echo "🔍 DRY-RUN — 실제 변경 없음. --apply 플래그로 적용하세요."
  echo ""
  echo "실행될 명령:"
  echo "  kubectl set env deployment/$DEPLOYMENT -n $NS ${ENV_VAR}=${TARGET_VALUE}"
  echo "  kubectl rollout status deployment/$DEPLOYMENT -n $NS --timeout=120s"
  exit 0
fi

echo "🔧 적용 중..."
kubectl set env "deployment/$DEPLOYMENT" -n "$NS" "${ENV_VAR}=${TARGET_VALUE}"
echo "  ✅ env 패치 완료"

echo "  rollout 대기 중..."
kubectl rollout status "deployment/$DEPLOYMENT" -n "$NS" --timeout=120s
echo "  ✅ rollout 완료"
echo ""
echo "✅ T20 proxy ${action} 완료."
