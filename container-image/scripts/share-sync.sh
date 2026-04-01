#!/bin/bash
# =============================================================================
# share-sync.sh — 공유 데이터 실시간 심링크 동기화 (60초 주기)
# =============================================================================
#
# 아키텍처:
#   모든 Pod에 /home/node/.efs-users/ 가 EFS users/ 디렉토리로 readOnly 마운트됨.
#   이 스크립트는 Auth Gateway API를 조회하여 현재 사용자에게 공유된 데이터셋 목록을
#   가져온 후, ~/workspace/team/{owner}/{name} 심링크를 생성/삭제한다.
#
#   공유 추가/해제 시 Pod 재시작 없이 최대 60초 내에 반영된다.
#
# 보안:
#   - .efs-users/ 는 readOnly 마운트 (다른 사용자 데이터 수정 불가)
#   - 심링크는 API에서 허가된 공유만 생성
#   - .efs-users/ 경로는 CLAUDE.md에 노출하지 않음
#
# 환경변수:
#   AUTH_GATEWAY_URL — Auth Gateway 내부 URL (필수)
#   USER_ID          — 현재 사용자 사번 (필수)
# =============================================================================

set -uo pipefail

USER_ID_LOWER=$(echo "${USER_ID}" | tr '[:upper:]' '[:lower:]')
EFS_USERS="/home/node/.efs-users"
TEAM_DIR="/home/node/workspace/team"
SYNC_INTERVAL="${SHARE_SYNC_INTERVAL:-60}"
POD_NAME="claude-terminal-${USER_ID_LOWER}"

# API에서 나에게 공유된 데이터셋 목록 조회
fetch_shares() {
    curl -sf "${AUTH_GATEWAY_URL}/api/v1/files/shared-mounts/${USER_ID}" \
        -H "X-Pod-Name: ${POD_NAME}" \
        --max-time 5 2>/dev/null
}

# 심링크 동기화 메인 로직
sync_symlinks() {
    local response
    response=$(fetch_shares)

    # API 응답이 없으면 스킵 (네트워크 오류 등)
    if [ -z "$response" ]; then
        return
    fi

    # Python으로 JSON 파싱 + 심링크 관리
    python3 -c "
import json, sys, os

team_dir = '${TEAM_DIR}'
efs_users = '${EFS_USERS}'
data = json.load(sys.stdin)
expected_links = set()

# API 응답은 list[{owner_username, dataset_name, file_path}]
shares = data if isinstance(data, list) else data.get('mounts', [])

for share in shares:
    owner = share.get('owner_username', '').lower()
    name = share.get('dataset_name', '')
    if not owner or not name:
        continue

    # 심링크 대상: .efs-users/{owner}/shared-data/{name}
    target = os.path.join(efs_users, owner, 'shared-data', name)
    # 심링크 경로: ~/workspace/team/{owner}/{name}
    link_dir = os.path.join(team_dir, owner)
    link_path = os.path.join(link_dir, name)
    expected_links.add(link_path)

    # 대상 디렉토리가 EFS에 존재하고 아직 심링크가 없으면 생성
    if os.path.exists(target) and not os.path.exists(link_path):
        os.makedirs(link_dir, exist_ok=True)
        os.symlink(target, link_path)
        print(f'[share-sync] LINKED: {link_path} -> {target}')

# 해제된 공유의 심링크 삭제
if os.path.isdir(team_dir):
    for owner_dir_name in os.listdir(team_dir):
        full_owner = os.path.join(team_dir, owner_dir_name)
        if not os.path.isdir(full_owner):
            continue
        for entry in os.listdir(full_owner):
            full_path = os.path.join(full_owner, entry)
            if os.path.islink(full_path) and full_path not in expected_links:
                os.unlink(full_path)
                print(f'[share-sync] UNLINKED: {full_path}')
        # 소유자 디렉토리가 비었으면 삭제
        if not os.listdir(full_owner):
            os.rmdir(full_owner)
" <<< "$response" 2>/dev/null
}

# ---------------------------------------------------------------------------
# 메인 루프 — 60초 간격으로 동기화
# ---------------------------------------------------------------------------
echo "[share-sync] 시작: USER_ID=${USER_ID}, 간격=${SYNC_INTERVAL}초"

# 첫 실행은 10초 후 (Pod 기동 직후 API가 아직 준비 안 됐을 수 있음)
sleep 10
sync_symlinks

while true; do
    sleep "${SYNC_INTERVAL}"
    sync_symlinks
done
