#!/usr/bin/env bash
# P2-iter3 #1: 배포 파이프라인 가드 — placeholder secret 커밋 차단.
#
# 상용 K8s 매니페스트(infra/k8s/)에 `CHANGE_ME_*` 같은 placeholder 문자열이
# 남아 있으면 빌드 실패. 로컬 개발 전용(infra/local-dev/)은 제외한다.
#
# 사용:
#   bash scripts/check-no-placeholder.sh
#   → exit 0: 깨끗함 / exit 1: placeholder 발견
#
# CI/pre-commit 훅에 연결 권장.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGETS=("$REPO_ROOT/infra/k8s")
PATTERNS=(
    'CHANGE_ME'
    'REPLACE_ME'
    'TODO_SECRET'
)

violations=0
for dir in "${TARGETS[@]}"; do
    if [[ ! -d "$dir" ]]; then
        continue
    fi
    for pat in "${PATTERNS[@]}"; do
        if grep -RIn --include='*.yaml' --include='*.yml' -E "$pat" "$dir" 2>/dev/null; then
            violations=$((violations + 1))
        fi
    done
done

if (( violations > 0 )); then
    echo ""
    echo "❌ placeholder secret 발견 — 커밋 전 실제 값으로 교체하거나 Secret 리소스를"
    echo "   파이프라인 프로비저닝으로 분리하세요 (infra/k8s/platform/onlyoffice.yaml 참조)."
    exit 1
fi

echo "✅ infra/k8s 에 placeholder 없음"
