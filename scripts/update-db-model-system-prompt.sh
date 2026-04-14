#!/bin/bash
# =============================================================================
# Claude Sonnet 4.6 (DB) 커스텀 모델의 system prompt 갱신 스크립트
#
# infra/openwebui-tools/db_system_prompt.md 내용을 모델 params.system 으로 주입.
# 환각 억제 + 정확한 스키마 기반 쿼리 생성을 위함 (Claude Code `/db` 스킬과 동일 지식).
# =============================================================================

set -euo pipefail

PROD_CTX="${PROD_CTX:-arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks}"
ADMIN_USER_ID="976d6a8c-bcba-4973-ab09-336e919e83d9"
MODEL_ID="claude-sonnet-4-6-db"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROMPT_MD="$SCRIPT_DIR/../infra/openwebui-tools/db_system_prompt.md"

[ -f "$PROMPT_MD" ] || { echo "missing: $PROMPT_MD"; exit 1; }

echo "[1/2] Admin JWT 발급..."
TOKEN=$(kubectl --context="$PROD_CTX" exec -n openwebui deploy/open-webui -- python3 -c "
import jwt, time
SECRET = open('/app/backend/.webui_secret_key').read().strip()
print(jwt.encode({'id': '$ADMIN_USER_ID', 'exp': int(time.time())+3600}, SECRET, algorithm='HS256'))
" 2>/dev/null | tail -1)

echo "[2/2] 모델 $MODEL_ID params.system 갱신..."
PROMPT_B64=$(base64 < "$PROMPT_MD" | tr -d '\n')

kubectl --context="$PROD_CTX" exec -n openwebui deploy/open-webui -- python3 -c "
import base64, json, urllib.request

token = '$TOKEN'
system_prompt = base64.b64decode('$PROMPT_B64').decode()

payload = {
    'id': '$MODEL_ID',
    'base_model_id': 'bedrock_ag_pipe.global.anthropic.claude-sonnet-4-6',
    'name': 'Claude Sonnet 4.6 (DB)',
    'meta': {
        'profile_image_url': '/static/favicon.png',
        'description': 'Sonnet 4.6 + DB Query tool 자동 활성화 + 스키마 지식 내장',
        'toolIds': ['db_query'],
        'capabilities': {'citations': True},
    },
    'params': {
        'system': system_prompt,
    },
    'is_active': True,
}

url = 'http://localhost:8080/api/v1/models/model/update?id=$MODEL_ID'
req = urllib.request.Request(
    url,
    method='POST',
    data=json.dumps(payload).encode('utf-8'),
    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
)
try:
    resp = urllib.request.urlopen(req, timeout=15).read()
    print('update: OK')
    d = json.loads(resp)
    print('  name :', d.get('name'))
    print('  tools:', d.get('meta', {}).get('toolIds'))
    print('  system len:', len((d.get('params') or {}).get('system', '')))
except urllib.error.HTTPError as e:
    print(f'update: HTTP {e.code}')
    print(e.read().decode()[:400])
"

echo ""
echo "완료. ai-chat.skons.net 새로고침 → 'Claude Sonnet 4.6 (DB)' 재선택 시 반영."
