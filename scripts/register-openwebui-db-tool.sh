#!/bin/bash
# =============================================================================
# Open WebUI DB Tool 등록 스크립트
#
# infra/openwebui-tools/db_tools.py 내용을 Open WebUI Tools API에 등록한다.
# 등록되면 일반 사용자가 자연어로 DB 관련 질문을 할 때 모델이 해당 tool을 호출.
# =============================================================================

set -euo pipefail

PROD_CTX="${PROD_CTX:-arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks}"
ADMIN_USER_ID="976d6a8c-bcba-4973-ab09-336e919e83d9"  # admin@skons.net
TOOL_ID="db_query"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL_PY="$SCRIPT_DIR/../infra/openwebui-tools/db_tools.py"

if [ ! -f "$TOOL_PY" ]; then
  echo "Tool source not found: $TOOL_PY"; exit 1
fi

# 1. Admin JWT
echo "[1/3] Admin JWT 발급..."
TOKEN=$(kubectl --context="$PROD_CTX" exec -n openwebui deploy/open-webui -- python3 -c "
import jwt, time
SECRET = open('/app/backend/.webui_secret_key').read().strip()
print(jwt.encode({'id': '$ADMIN_USER_ID', 'exp': int(time.time())+3600}, SECRET, algorithm='HS256'))
" 2>/dev/null | tail -1)
[ -n "$TOKEN" ] || { echo "JWT 실패"; exit 1; }
echo "  OK"

# 2. Payload 생성 + 3. 등록/갱신 (Pod 안에서 Python으로 수행)
echo "[2-3/3] Tool 등록..."
TOOL_CONTENT_B64=$(base64 < "$TOOL_PY" | tr -d '\n')
kubectl --context="$PROD_CTX" exec -n openwebui deploy/open-webui -- python3 -c "
import base64, json, os, urllib.request

tool_id = '$TOOL_ID'
token = '$TOKEN'
content = base64.b64decode('$TOOL_CONTENT_B64').decode()

# 존재 확인
try:
    req = urllib.request.Request(f'http://localhost:8080/api/v1/tools/id/{tool_id}', headers={'Authorization': f'Bearer {token}'})
    urllib.request.urlopen(req, timeout=10).read()
    exists = True
except Exception:
    exists = False

if exists:
    url = f'http://localhost:8080/api/v1/tools/id/{tool_id}/update'
    action = 'update'
else:
    url = 'http://localhost:8080/api/v1/tools/create'
    action = 'create'

payload = {
    'id': tool_id,
    'name': 'DB Query',
    'meta': {'description': '사내 DB 조회 도구 (TANGO 알람/Safety/DocuLog)'},
    'content': content,
}
req = urllib.request.Request(
    url,
    method='POST',
    data=json.dumps(payload).encode('utf-8'),
    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
)
try:
    resp = urllib.request.urlopen(req, timeout=15).read()
    print(f'{action}: OK')
    print(resp.decode()[:400])
except urllib.error.HTTPError as e:
    print(f'{action}: HTTP {e.code}')
    print(e.read().decode()[:400])
"

echo ""
echo "등록 완료. Open WebUI Workspace > Tools 에서 'DB Query' 확인."
