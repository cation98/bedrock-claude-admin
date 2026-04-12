#!/usr/bin/env bash
# =============================================================================
# T2 apply 완료 후 ElastiCache endpoint 확정 → NetworkPolicy + ExternalName 갱신
#
# 실행 시점: devops로부터 terraform output 수신 직후
#
# 입력: $1 = primary endpoint DNS (ex: xxx.yyy.ng.0001.apse2.cache.amazonaws.com)
#       $2 = reader endpoint DNS  (ex: xxx.yyy.ng.0001.apse2.cache.amazonaws.com)
#
# 사용법:
#   chmod +x docs/qa/t2-post-apply-redis.sh
#   ./docs/qa/t2-post-apply-redis.sh <primary_endpoint> [reader_endpoint]
#
# 처리 내용:
#   1. endpoint DNS → IP 해석
#   2. elasticache-service.yaml externalName 교체 → kubectl apply
#   3. network-policy.yaml ipBlock을 /24 → /32로 정밀화
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <primary_endpoint> [reader_endpoint]"
  echo "  primary_endpoint: terraform output redis_primary_endpoint 값"
  echo "  reader_endpoint:  terraform output redis_reader_endpoint 값 (선택)"
  exit 1
fi

PRIMARY_ENDPOINT="$1"
READER_ENDPOINT="${2:-}"

echo "[INFO] Primary endpoint: $PRIMARY_ENDPOINT"

# ── 1. DNS → IP 해석 ──────────────────────────────────────────────────────────
PRIMARY_IP=$(dig +short "$PRIMARY_ENDPOINT" | head -1)
if [[ -z "$PRIMARY_IP" ]]; then
  echo -e "${RED}[FAIL]${NC} DNS 해석 실패: $PRIMARY_ENDPOINT"
  echo "       VPN 또는 bastion에서 실행하고 있는지 확인하세요"
  exit 1
fi
echo -e "${GREEN}[INFO]${NC} Primary IP: $PRIMARY_IP"

if [[ -n "$READER_ENDPOINT" ]]; then
  READER_IP=$(dig +short "$READER_ENDPOINT" | head -1)
  echo -e "${GREEN}[INFO]${NC} Reader IP:  $READER_IP"
fi

# ── 2. elasticache-service.yaml ExternalName 교체 ────────────────────────────
SVCFILE="infra/k8s/openwebui/elasticache-service.yaml"

sed -i.bak \
  "s|PLACEHOLDER_REPLACE_WITH_TERRAFORM_OUTPUT$|$PRIMARY_ENDPOINT|" \
  "$SVCFILE"

if [[ -n "$READER_ENDPOINT" ]]; then
  sed -i.bak \
    "s|PLACEHOLDER_REPLACE_WITH_TERRAFORM_OUTPUT_READER|$READER_ENDPOINT|" \
    "$SVCFILE"
fi

echo -e "${GREEN}[INFO]${NC} elasticache-service.yaml 업데이트 완료"
kubectl apply -f "$SVCFILE"
echo -e "${GREEN}[PASS]${NC} ExternalName Service 적용"

# ── 3. NetworkPolicy ipBlock /24 → /32 정밀화 ────────────────────────────────
# 현재: 10.0.10.0/24, 10.0.20.0/24 (서브넷 전체 허용)
# 변경: ElastiCache primary IP/32 (단일 호스트만 허용)
NP_FILE="infra/k8s/openwebui/network-policy.yaml"

echo ""
echo "[INFO] NetworkPolicy ipBlock 정밀화: /24 → ${PRIMARY_IP}/32"
echo "[INFO] Primary: $PRIMARY_IP/32"
[[ -n "${READER_IP:-}" ]] && echo "[INFO] Reader: $READER_IP/32 (replica failover 고려)"

# 현재 ipBlock 규칙을 /32로 교체하려면 수동 편집 또는 아래 지시 따를 것
echo ""
echo -e "${YELLOW}[ACTION REQUIRED]${NC} 아래 ipBlock 값을 network-policy.yaml에서 수정:"
echo "  (allow-bedrock-ag-traffic, allow-pipelines-traffic 두 곳 모두)"
echo ""
echo "  기존:"
echo "    - ipBlock:"
echo "        cidr: 10.0.10.0/24"
echo "    - ipBlock:"
echo "        cidr: 10.0.20.0/24"
echo ""
echo "  변경 후:"
echo "    - ipBlock:"
echo "        cidr: ${PRIMARY_IP}/32   # ElastiCache primary"
[[ -n "${READER_IP:-}" ]] && \
echo "    - ipBlock:" && \
echo "        cidr: ${READER_IP}/32   # ElastiCache reader (failover 대비)"
echo ""
echo "  수정 후: kubectl apply -f infra/k8s/openwebui/network-policy.yaml"
echo ""
echo "  ⚠️  자동 교체는 multi-line YAML 편집 위험으로 수동 확인 권장"

rm -f "${SVCFILE}.bak"
echo ""
echo -e "${GREEN}[DONE]${NC} T2 post-apply Redis 갱신 완료. network-policy.yaml 수동 확인 후 apply 필요."
