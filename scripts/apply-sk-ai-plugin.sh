#!/usr/bin/env bash
# SK AI OnlyOffice Plugin — ConfigMap 생성 + 배포 helper
#
# 목적: container-image/onlyoffice-plugins/sk-ai/ 소스 파일을
#       Kubernetes ConfigMap(sk-ai-plugin-src, sk-ai-plugin-meta)으로 변환 후
#       claude-sessions namespace에 적용.
#
# 사용법:
#   ./scripts/apply-sk-ai-plugin.sh [--context docker-desktop]
#   ./scripts/apply-sk-ai-plugin.sh --dry-run               # kubectl apply 없이 출력만
#
# 실행 후 onlyoffice pod rollout restart가 필요하면:
#   kubectl -n claude-sessions rollout restart deploy/onlyoffice
#
# 주의: OO pod에 initContainer + volumes + volumeMount가 선행 적용돼야 함.
#       infra/k8s/platform/onlyoffice.yaml 참조.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLUGIN_DIR="${REPO_ROOT}/container-image/onlyoffice-plugins/sk-ai"
NAMESPACE="claude-sessions"

KUBECTL_CONTEXT=""
DRY_RUN=""

# ── CLI 파싱 ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)
      KUBECTL_CONTEXT="--context=$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="--dry-run=client -o yaml"
      shift
      ;;
    *)
      echo "Unknown arg: $1" >&2
      echo "Usage: $0 [--context <name>] [--dry-run]" >&2
      exit 1
      ;;
  esac
done

# ── 사전 검증 ─────────────────────────────────────────────────────────────
for f in config.json index.html scripts/code.js; do
  if [[ ! -f "${PLUGIN_DIR}/${f}" ]]; then
    echo "ERROR: plugin file missing: ${PLUGIN_DIR}/${f}" >&2
    exit 1
  fi
done

# config.js 생성 (config.js.template 있으면 복사, 없으면 기본값)
CONFIG_JS="${PLUGIN_DIR}/config.js"
if [[ ! -f "${CONFIG_JS}" ]]; then
  if [[ -f "${PLUGIN_DIR}/config.js.template" ]]; then
    cp "${PLUGIN_DIR}/config.js.template" "${CONFIG_JS}"
  else
    cat > "${CONFIG_JS}" <<'EOF'
window.SKAI_CONFIG = {
  aiEndpoint: "/api/v1/ai/chat/completions",
  model:      "claude-sonnet-4-6"
};
EOF
  fi
fi

echo "=== Applying SK AI plugin ConfigMaps to namespace '${NAMESPACE}' ==="
[[ -n "${KUBECTL_CONTEXT}" ]] && echo "  context: ${KUBECTL_CONTEXT}"
[[ -n "${DRY_RUN}"       ]] && echo "  mode: dry-run (output only)"

# ── ConfigMap A: meta (config.json + config.js) ──────────────────────────
kubectl ${KUBECTL_CONTEXT} -n "${NAMESPACE}" create configmap sk-ai-plugin-meta \
  --from-file=config.json="${PLUGIN_DIR}/config.json" \
  --from-file=config.js="${CONFIG_JS}" \
  ${DRY_RUN:---dry-run=client -o yaml} | kubectl ${KUBECTL_CONTEXT} ${DRY_RUN:+-n "${NAMESPACE}"} apply -f - ${DRY_RUN:+ || cat}

# ── ConfigMap B: src (index.html + code.js) ──────────────────────────────
kubectl ${KUBECTL_CONTEXT} -n "${NAMESPACE}" create configmap sk-ai-plugin-src \
  --from-file=index.html="${PLUGIN_DIR}/index.html" \
  --from-file=code.js="${PLUGIN_DIR}/scripts/code.js" \
  ${DRY_RUN:---dry-run=client -o yaml} | kubectl ${KUBECTL_CONTEXT} ${DRY_RUN:+-n "${NAMESPACE}"} apply -f - ${DRY_RUN:+ || cat}

echo "=== Done ==="
echo "Next steps:"
echo "  1. (한 번만) infra/k8s/platform/onlyoffice.yaml 에 initContainer/volumes 패치 apply"
echo "  2. kubectl ${KUBECTL_CONTEXT} -n ${NAMESPACE} rollout restart deploy/onlyoffice"
echo "  3. OO 에디터 로드 → 플러그인 탭에서 'SK AI' 메뉴 확인"
