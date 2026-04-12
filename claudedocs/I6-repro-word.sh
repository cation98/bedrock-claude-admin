#!/usr/bin/env bash
# I6 재현 스크립트 — OnlyOffice config endpoint Word/PPTX 호출
#
# 목적: auth-gateway의 OnlyOffice config API가 Word/PPTX 파일에 대해 올바른
#       JSON config를 반환하는지 검증.
#
# 실행 방법 (user가 직접 실행):
#   1. AUTH_TOKEN 에 유효한 JWT Bearer 토큰 설정
#   2. BASE_URL 에 auth-gateway URL 설정
#   3. bash claudedocs/I6-repro-word.sh
#
# 주의: 이 스크립트는 dry-run 용도. 실제 실행 시 auth-gateway가 실행 중이어야 함.
# ============================================================================

set -euo pipefail

# ---- 설정 (실행 전 교체) ---------------------------------------------------
BASE_URL="${BASE_URL:-http://localhost:8000}"
AUTH_TOKEN="${AUTH_TOKEN:-<REPLACE_WITH_VALID_JWT>}"

HEADER_AUTH="Authorization: Bearer ${AUTH_TOKEN}"
HEADER_JSON="Content-Type: application/json"

echo "=== I6 Word/PPTX OnlyOffice Config Endpoint 재현 테스트 ==="
echo "BASE_URL: ${BASE_URL}"
echo ""

# ============================================================================
# [1] Word (.docx) — config API — GET
# 기대: 200 OK, documentType="word", fileType="docx"
# ============================================================================
echo "--- [1] GET /config/document.docx ---"
curl -s -o /tmp/I6-docx-config.json -w "HTTP %{http_code}\n" \
  -H "${HEADER_AUTH}" \
  "${BASE_URL}/api/v1/viewers/onlyoffice/config/document.docx"

echo "응답 (config 구조 검증):"
python3 -c "
import json, sys
try:
    with open('/tmp/I6-docx-config.json') as f:
        d = json.load(f)
    assert d.get('documentType') == 'word', f'documentType mismatch: {d.get(\"documentType\")}'
    assert d['document']['fileType'] == 'docx', f'fileType mismatch: {d[\"document\"][\"fileType\"]}'
    print('  [OK] documentType=word, fileType=docx')
    print(f'  editorConfig.mode = {d[\"editorConfig\"][\"mode\"]}')
    if 'token' in d:
        print('  [OK] JWT token present')
    else:
        print('  [WARN] JWT token absent')
except Exception as e:
    print(f'  [FAIL] {e}')
    with open('/tmp/I6-docx-config.json') as f:
        print(f.read()[:300])
"
echo ""

# ============================================================================
# [2] PPTX (.pptx) — config API — GET
# 기대: 200 OK, documentType="slide", fileType="pptx"
# ============================================================================
echo "--- [2] GET /config/presentation.pptx ---"
curl -s -o /tmp/I6-pptx-config.json -w "HTTP %{http_code}\n" \
  -H "${HEADER_AUTH}" \
  "${BASE_URL}/api/v1/viewers/onlyoffice/config/presentation.pptx"

echo "응답:"
python3 -c "
import json
try:
    with open('/tmp/I6-pptx-config.json') as f:
        d = json.load(f)
    assert d.get('documentType') == 'slide', f'documentType mismatch: {d.get(\"documentType\")}'
    assert d['document']['fileType'] == 'pptx', f'fileType mismatch'
    print('  [OK] documentType=slide, fileType=pptx')
    print(f'  editorConfig.mode = {d[\"editorConfig\"][\"mode\"]}')
except Exception as e:
    print(f'  [FAIL] {e}')
"
echo ""

# ============================================================================
# [3] Word — /edit 엔드포인트 (편집 모드 HTML 반환)
# 기대: 200 OK, HTML에 config JSON 포함, mode="edit", permissions.edit=true
# ============================================================================
echo "--- [3] GET /edit/TESTUSER01/report.docx (편집 모드) ---"
curl -s -o /tmp/I6-docx-edit.html -w "HTTP %{http_code}\n" \
  -H "${HEADER_AUTH}" \
  "${BASE_URL}/api/v1/viewers/onlyoffice/edit/TESTUSER01/report.docx"

python3 -c "
import re, json
with open('/tmp/I6-docx-edit.html') as f:
    html = f.read()
m = re.search(r'var config = (\{.*?\});\s*config\.type', html, re.DOTALL)
if not m:
    print('  [FAIL] config JSON not found in HTML')
    print(html[:400])
else:
    cfg = json.loads(m.group(1))
    mode = cfg['editorConfig']['mode']
    edit_perm = cfg['document']['permissions']['edit']
    print(f'  mode={mode}, permissions.edit={edit_perm}')
    if mode == 'edit' and edit_perm:
        print('  [OK] Word edit mode correct')
    else:
        print('  [FAIL] Expected edit mode with edit=True')
"
echo ""

# ============================================================================
# [4] PPTX — /edit 엔드포인트 (편집 모드)
# 기대: 200 OK, mode="edit", customization.forcesave=true
# ============================================================================
echo "--- [4] GET /edit/TESTUSER01/deck.pptx (편집 모드) ---"
curl -s -o /tmp/I6-pptx-edit.html -w "HTTP %{http_code}\n" \
  -H "${HEADER_AUTH}" \
  "${BASE_URL}/api/v1/viewers/onlyoffice/edit/TESTUSER01/deck.pptx"

python3 -c "
import re, json
with open('/tmp/I6-pptx-edit.html') as f:
    html = f.read()
m = re.search(r'var config = (\{.*?\});\s*config\.type', html, re.DOTALL)
if not m:
    print('  [FAIL] config JSON not found in HTML')
else:
    cfg = json.loads(m.group(1))
    forcesave = cfg['editorConfig']['customization'].get('forcesave')
    print(f'  customization.forcesave={forcesave}')
    if forcesave:
        print('  [OK] PPTX forcesave enabled')
    else:
        print('  [FAIL] forcesave should be True for PPTX edit mode')
"
echo ""

# ============================================================================
# [5] Word — 콜백 시뮬레이션 (status=2, dry-run)
# 실제 실행 전 NOTE: document_key는 미리 /edit 열어서 생성된 세션의 key를 사용해야 함
# OO_JWT_SECRET 도 auth-gateway 환경에서 가져와야 함
# ============================================================================
echo "--- [5] POST /callback (Word status=2 시뮬레이션) --- [DRY-RUN 확인만]"
echo "  NOTE: 실제 실행하려면 OO_JWT_SECRET과 유효한 document_key 필요."
echo "  아래는 구조 확인용 — JWT 없이 보내면 401/400 반환 예상"

curl -s -o /tmp/I6-callback-docx.json -w "HTTP %{http_code}\n" \
  -H "${HEADER_JSON}" \
  -X POST \
  "${BASE_URL}/api/v1/viewers/onlyoffice/callback" \
  -d '{
    "status": 2,
    "key": "<REPLACE_WITH_SESSION_KEY>",
    "url": "http://localhost/cache/files/Editor.docx?token=<oo_token>",
    "filetype": "docx"
  }'

echo "  (JWT 없는 요청 → 401 예상이면 정상)"
echo ""

# ============================================================================
# [6] PPTX — 콜백 시뮬레이션 (status=2, dry-run)
# ============================================================================
echo "--- [6] POST /callback (PPTX status=2 시뮬레이션) --- [DRY-RUN]"
curl -s -o /tmp/I6-callback-pptx.json -w "HTTP %{http_code}\n" \
  -H "${HEADER_JSON}" \
  -X POST \
  "${BASE_URL}/api/v1/viewers/onlyoffice/callback" \
  -d '{
    "status": 2,
    "key": "<REPLACE_WITH_SESSION_KEY>",
    "url": "http://localhost/cache/files/Slide.pptx?token=<oo_token>",
    "filetype": "pptx"
  }'

echo ""
echo "=== 완료. 임시 파일: /tmp/I6-*.json /tmp/I6-*.html ==="
