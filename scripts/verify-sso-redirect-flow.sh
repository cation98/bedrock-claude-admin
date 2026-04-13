#!/usr/bin/env bash
# Phase 1a: SSO → chat.skons.net → auth.skons.net → portal 복귀 SameSite 영향 검증
# Usage: AUTH_GATEWAY_URL=https://auth.skons.net OPEN_WEBUI_URL=https://chat.skons.net ./scripts/verify-sso-redirect-flow.sh

set -euo pipefail

AUTH=${AUTH_GATEWAY_URL:-https://auth.skons.net}
OW=${OPEN_WEBUI_URL:-https://chat.skons.net}
USER=${TEST_USER:-TESTUSER01}
PASS=${TEST_PASS:-test2026}

echo "=== [1] Unauthenticated chat.skons.net → /auth/expired redirect ==="
code=$(curl -sk -o /dev/null -w "%{http_code}" "$OW/")
echo "    GET $OW/ → status=$code (expected: 302 or 401 without cookie)"

echo ""
echo "=== [2] SSO login — auth.skons.net cookie 수신 ==="
cookie_jar=$(mktemp)
login_resp=$(curl -sk -c "$cookie_jar" -X POST "$AUTH/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}")

if echo "$login_resp" | grep -q access_token; then
  echo "    로그인 성공"
else
  echo "    로그인 실패 — TEST_USER/TEST_PASS + ALLOW_TEST_USERS=true ConfigMap 확인"
  exit 1
fi

echo ""
echo "=== [3] 쿠키 속성 검증 ==="
grep -E "bedrock_jwt|bedrock_jwt_vis" "$cookie_jar"

echo ""
echo "=== [4] Lax 하 cross-site navigation (GET) — 정상 기대 ==="
code=$(curl -sk -b "$cookie_jar" -o /dev/null -w "%{http_code}" "$OW/")
echo "    Lax cross-site GET → status=$code (expected: 200 or 302, not 401)"

echo ""
echo "=== [5] Strict 시뮬레이션 — Sec-Fetch-Site: cross-site 헤더 ==="
# Strict 모드에서는 cross-site navigation 시 쿠키 전송 안 됨 → 401 기대
# 참고: curl 단독으로는 브라우저 SameSite 쿠키 차단을 완전히 재현하지 못함.
# 브라우저 실제 동작은 SameSite 쿠키 속성이 결정하며, Sec-Fetch-Site 헤더는
# 서버 측 참조 정보일 뿐 쿠키 전송 여부를 직접 차단하지 않음.
code=$(curl -sk -b "$cookie_jar" -H "Sec-Fetch-Site: cross-site" \
  -o /dev/null -w "%{http_code}" "$OW/")
echo "    Strict simulation → status=$code (expected in Strict: 401; in Lax: 200/302)"

rm -f "$cookie_jar"

echo ""
echo "=== 결론 ==="
echo "Lax 유지 시 step 4가 200/302, step 5도 200/302 (Sec-Fetch-Site 헤더 단독으로는 쿠키 차단하지 않음)"
echo "브라우저 실제 동작은 Sec-Fetch-Site가 아니라 SameSite 쿠키 속성이 결정함"
echo "실 환경 결정 근거는 docs/decisions/phase1a-samesite-strict-vs-lax.md 참조"
