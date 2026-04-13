#!/usr/bin/env bash
# =============================================================================
# NetworkPolicy ipBlock Drift Scanner (Phase 1c Backlog B5)
# =============================================================================
# 목적: NetworkPolicy egress 대상이 cluster-external 서비스(ElastiCache/RDS)
#   일 때 반드시 ipBlock CIDR을 사용해야 한다. podSelector는 외부 서비스에
#   작동하지 않으므로 drift 경고를 발생시킨다.
#
# 규칙:
#   - NP에서 `elasticache|redis|rds|postgres|external` 관련 egress rule은
#     `ipBlock:` 필드를 포함해야 함. podSelector/namespaceSelector만으로
#     구성된 경우 drift로 간주.
#
# 종료 코드:
#   0 — drift 없음
#   1 — drift 탐지 (CI 실패 유도)
# =============================================================================

set -euo pipefail

cd "$(dirname "$0")/.."

NP_DIRS=("infra/k8s")
DRIFT_FOUND=0

# grep pattern으로 external service 힌트 탐지 + ipBlock 존재 확인
EXTERNAL_HINTS='(elasticache|redis|rds|postgres|cache\.amazonaws)'

while IFS= read -r file; do
    # manifest에 external service 힌트 있는지 확인
    if ! grep -Eqi "${EXTERNAL_HINTS}" "$file"; then
        continue
    fi

    # NetworkPolicy 리소스인지 확인
    if ! grep -q "kind: NetworkPolicy" "$file"; then
        continue
    fi

    # ipBlock 미사용 시 drift
    if ! grep -q "ipBlock:" "$file"; then
        echo "DRIFT: $file — external service hint 존재하나 ipBlock 미사용"
        DRIFT_FOUND=1
    fi
done < <(find "${NP_DIRS[@]}" -type f -name "*.yaml" 2>/dev/null)

if [ "${DRIFT_FOUND}" -eq 0 ]; then
    echo "OK: NetworkPolicy ipBlock drift 없음"
    exit 0
else
    echo ""
    echo "See: claudedocs/np-ipblock-rule.md (ipBlock 필수 케이스 참고)"
    exit 1
fi
