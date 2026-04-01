#!/bin/bash
# =============================================================================
# deploy -- 웹앱을 플랫폼에 배포합니다
#
# 사용법:
#   deploy <앱이름>                          기본 배포
#   deploy <앱이름> --acl "user1,user2"      접근 허용 사용자 지정
#   deploy <앱이름> --rollback <버전>        이전 버전으로 롤백
#   deploy <앱이름> --undeploy               앱 삭제 (배포 해제)
#
# 앱 소스: ~/apps/<앱이름>/
# 스냅샷:  ~/deployed/<앱이름>/v-YYYYMMDD-HHMM/
# 심링크:  ~/deployed/<앱이름>/current -> 최신 버전
#
# 인증: Pod 내부 → auth-gateway 내부 API (X-Pod-Name 헤더)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# 색상 및 출력 헬퍼
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[deploy]${NC} $*"; }
success() { echo -e "${GREEN}[deploy]${NC} $*"; }
warn()    { echo -e "${YELLOW}[deploy]${NC} $*"; }
error()   { echo -e "${RED}[deploy]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
# 사용법 출력
# ---------------------------------------------------------------------------
usage() {
    cat << 'EOF'

  deploy -- 웹앱 배포 도구

  사용법:
    deploy <앱이름>                          앱 배포
    deploy <앱이름> --acl "user1,user2"      접근 허용 사용자 지정
    deploy <앱이름> --rollback <버전>        이전 버전으로 롤백
    deploy <앱이름> --undeploy               앱 삭제

  예시:
    deploy my-dashboard
    deploy my-dashboard --acl "N1102359,N1234567"
    deploy my-dashboard --rollback v-20260401-1430
    deploy my-dashboard --undeploy

  앱 소스 위치:  ~/apps/<앱이름>/
  배포 스냅샷:   ~/deployed/<앱이름>/

EOF
    exit 1
}

# ---------------------------------------------------------------------------
# 인수 파싱
# ---------------------------------------------------------------------------
APP_NAME=""
ACL_USERS=""
ROLLBACK_VERSION=""
DO_UNDEPLOY=false

if [ $# -lt 1 ]; then
    usage
fi

APP_NAME="$1"
shift

while [ $# -gt 0 ]; do
    case "$1" in
        --acl)
            if [ -z "${2:-}" ]; then
                error "--acl 옵션에 사용자 목록이 필요합니다 (예: --acl \"user1,user2\")"
                exit 1
            fi
            ACL_USERS="$2"
            shift 2
            ;;
        --rollback)
            if [ -z "${2:-}" ]; then
                error "--rollback 옵션에 버전이 필요합니다 (예: --rollback v-20260401-1430)"
                exit 1
            fi
            ROLLBACK_VERSION="$2"
            shift 2
            ;;
        --undeploy)
            DO_UNDEPLOY=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            error "알 수 없는 옵션: $1"
            usage
            ;;
    esac
done

# ---------------------------------------------------------------------------
# 환경 검증
# ---------------------------------------------------------------------------
AUTH_GATEWAY_URL="${AUTH_GATEWAY_URL:-}"
POD_NAME="${HOSTNAME:-}"

if [ -z "${AUTH_GATEWAY_URL}" ]; then
    error "AUTH_GATEWAY_URL 환경변수가 설정되지 않았습니다."
    error "이 명령은 Claude Code 터미널 Pod 내부에서만 사용할 수 있습니다."
    exit 1
fi

if [ -z "${POD_NAME}" ]; then
    error "HOSTNAME 환경변수가 설정되지 않았습니다."
    exit 1
fi

# 앱 이름 유효성 검사 (영문 소문자, 숫자, 하이픈만 허용)
if ! echo "${APP_NAME}" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$'; then
    error "앱 이름은 영문 소문자, 숫자, 하이픈(-)만 사용할 수 있습니다."
    error "  올바른 예: my-dashboard, api-server, app1"
    error "  잘못된 예: My_App, app name, -app-"
    exit 1
fi

APP_DIR="${HOME}/apps/${APP_NAME}"
DEPLOY_DIR="${HOME}/deployed/${APP_NAME}"

# ===========================================================================
# 배포 해제 (--undeploy)
# ===========================================================================
if [ "${DO_UNDEPLOY}" = true ]; then
    echo ""
    echo "============================================"
    echo "  앱 배포 해제: ${APP_NAME}"
    echo "============================================"
    echo ""

    warn "이 작업은 배포된 앱을 삭제합니다. 소스 코드(~/apps/${APP_NAME})는 유지됩니다."
    read -r -p "계속하시겠습니까? (y/N): " confirm
    if [ "${confirm}" != "y" ] && [ "${confirm}" != "Y" ]; then
        info "취소되었습니다."
        exit 0
    fi

    info "auth-gateway에 삭제 요청 중..."

    HTTP_CODE=$(curl -sf -o /tmp/deploy-response.json -w "%{http_code}" \
        -X DELETE "${AUTH_GATEWAY_URL}/api/v1/apps/${APP_NAME}" \
        -H "X-Pod-Name: ${POD_NAME}" \
        -H "Content-Type: application/json" \
        --max-time 30 2>/dev/null) || HTTP_CODE="000"

    if [ "${HTTP_CODE}" = "200" ] || [ "${HTTP_CODE}" = "204" ]; then
        success "앱이 성공적으로 삭제되었습니다."
        # 로컬 deployed 디렉토리 정리 (소스는 유지)
        if [ -d "${DEPLOY_DIR}" ]; then
            rm -rf "${DEPLOY_DIR}"
            info "로컬 스냅샷 디렉토리 정리 완료"
        fi
    else
        error "삭제 실패 (HTTP ${HTTP_CODE})"
        [ -f /tmp/deploy-response.json ] && cat /tmp/deploy-response.json >&2
        exit 1
    fi

    exit 0
fi

# ===========================================================================
# 롤백 (--rollback)
# ===========================================================================
if [ -n "${ROLLBACK_VERSION}" ]; then
    echo ""
    echo "============================================"
    echo "  롤백: ${APP_NAME} -> ${ROLLBACK_VERSION}"
    echo "============================================"
    echo ""

    # 로컬 버전 존재 확인
    if [ ! -d "${DEPLOY_DIR}/${ROLLBACK_VERSION}" ]; then
        error "버전 '${ROLLBACK_VERSION}'을(를) 찾을 수 없습니다."
        echo ""
        info "사용 가능한 버전:"
        if [ -d "${DEPLOY_DIR}" ]; then
            ls -1d "${DEPLOY_DIR}"/v-* 2>/dev/null | while read -r dir; do
                basename "${dir}"
            done
        else
            echo "  (배포 이력 없음)"
        fi
        exit 1
    fi

    # 로컬 심링크 변경
    info "로컬 심링크를 ${ROLLBACK_VERSION}(으)로 변경 중..."
    ln -sfn "${ROLLBACK_VERSION}" "${DEPLOY_DIR}/current"

    # auth-gateway에 롤백 요청
    info "auth-gateway에 롤백 요청 중..."

    HTTP_CODE=$(curl -sf -o /tmp/deploy-response.json -w "%{http_code}" \
        -X POST "${AUTH_GATEWAY_URL}/api/v1/apps/${APP_NAME}/rollback" \
        -H "X-Pod-Name: ${POD_NAME}" \
        -H "Content-Type: application/json" \
        -d "{\"version\": \"${ROLLBACK_VERSION}\"}" \
        --max-time 30 2>/dev/null) || HTTP_CODE="000"

    if [ "${HTTP_CODE}" = "200" ]; then
        echo ""
        success "롤백 완료!"
        echo ""
        info "  앱:    ${APP_NAME}"
        info "  버전:  ${ROLLBACK_VERSION}"
        [ -f /tmp/deploy-response.json ] && {
            APP_URL=$(python3 -c "import json; d=json.load(open('/tmp/deploy-response.json')); print(d.get('app_url',''))" 2>/dev/null || true)
            [ -n "${APP_URL}" ] && info "  URL:   ${APP_URL}"
        }
    else
        error "롤백 실패 (HTTP ${HTTP_CODE})"
        [ -f /tmp/deploy-response.json ] && cat /tmp/deploy-response.json >&2
        exit 1
    fi

    exit 0
fi

# ===========================================================================
# 일반 배포
# ===========================================================================
echo ""
echo "============================================"
echo "  앱 배포: ${APP_NAME}"
echo "============================================"
echo ""

# ---------------------------------------------------------------------------
# 1) 소스 디렉토리 검증
# ---------------------------------------------------------------------------
if [ ! -d "${APP_DIR}" ]; then
    error "앱 디렉토리를 찾을 수 없습니다: ~/apps/${APP_NAME}"
    echo ""
    info "먼저 앱을 만들어주세요:"
    echo "  mkdir -p ~/apps/${APP_NAME}"
    echo "  cd ~/apps/${APP_NAME}"
    echo "  # 앱 코드 작성..."
    echo "  deploy ${APP_NAME}"
    exit 1
fi

# 실행 가능한 파일이 있는지 확인
HAS_RUNNABLE=false
for f in start.sh main.py app.py package.json server.js index.js; do
    if [ -f "${APP_DIR}/${f}" ]; then
        HAS_RUNNABLE=true
        info "감지된 앱 타입: ${f}"
        break
    fi
done

if [ "${HAS_RUNNABLE}" = false ]; then
    error "실행 가능한 앱 파일을 찾을 수 없습니다."
    echo ""
    info "~/apps/${APP_NAME}/ 에 다음 중 하나가 필요합니다:"
    echo "  - start.sh      (커스텀 시작 스크립트)"
    echo "  - main.py       (FastAPI -- uvicorn main:app)"
    echo "  - app.py        (FastAPI -- uvicorn app:app)"
    echo "  - package.json  (Node.js -- npm start)"
    echo "  - server.js     (Node.js)"
    echo "  - index.js      (Node.js)"
    exit 1
fi

# ---------------------------------------------------------------------------
# 2) Git 자동 관리
#    - .git 없으면 초기화
#    - 변경사항 있으면 자동 커밋
#    - 배포 태그 자동 생성
# ---------------------------------------------------------------------------
cd "${APP_DIR}"

TIMESTAMP=$(date +%Y%m%d-%H%M)
VERSION_TAG="v-${TIMESTAMP}"

if [ ! -d .git ]; then
    info "Git 저장소 초기화 중..."
    git init --quiet
    git add .
    git commit --quiet -m "initial: ${APP_NAME} 최초 배포"
fi

# 변경사항 확인 및 커밋
if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    info "변경사항 커밋 중..."
    git add .
    git commit --quiet -m "deploy: ${VERSION_TAG}" || true
fi

# 태그 생성
git tag "${VERSION_TAG}" 2>/dev/null || {
    # 같은 분에 재배포하면 태그 충돌 -- 초 단위 추가
    VERSION_TAG="v-${TIMESTAMP}-$(date +%S)"
    git tag "${VERSION_TAG}" 2>/dev/null || true
}

info "버전: ${VERSION_TAG}"

# ---------------------------------------------------------------------------
# 3) 배포 스냅샷 생성
#    - ~/deployed/{app_name}/v-{tag}/ 에 소스 복사
#    - .git, node_modules, __pycache__, uploads 등 제외
#    - current 심링크를 최신 버전으로 갱신
#    - data/ 디렉토리 생성 (앱의 영구 데이터 저장소)
# ---------------------------------------------------------------------------
SNAPSHOT_DIR="${DEPLOY_DIR}/${VERSION_TAG}"

info "배포 스냅샷 생성 중..."
mkdir -p "${SNAPSHOT_DIR}"

# rsync 사용 가능하면 rsync, 아니면 cp + 수동 제외
if command -v rsync &>/dev/null; then
    rsync -a \
        --exclude='.git' \
        --exclude='node_modules' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='uploads' \
        --exclude='.env' \
        "${APP_DIR}/" "${SNAPSHOT_DIR}/"
else
    cp -r "${APP_DIR}/"* "${SNAPSHOT_DIR}/" 2>/dev/null || true
    cp -r "${APP_DIR}/".* "${SNAPSHOT_DIR}/" 2>/dev/null || true
    # 불필요한 디렉토리 삭제
    rm -rf "${SNAPSHOT_DIR}/.git" \
           "${SNAPSHOT_DIR}/node_modules" \
           "${SNAPSHOT_DIR}/__pycache__" \
           "${SNAPSHOT_DIR}/uploads" \
           "${SNAPSHOT_DIR}/.env" 2>/dev/null || true
    find "${SNAPSHOT_DIR}" -name '*.pyc' -delete 2>/dev/null || true
fi

# current 심링크 갱신 (상대 경로 사용)
ln -sfn "${VERSION_TAG}" "${DEPLOY_DIR}/current"

# 영구 데이터 디렉토리 (버전 간 공유, 업로드 파일 등)
mkdir -p "${DEPLOY_DIR}/data"

info "스냅샷: ~/deployed/${APP_NAME}/${VERSION_TAG}/"

# ---------------------------------------------------------------------------
# 4) auth-gateway API 호출 (배포 등록)
#    Pod 내부 인증: X-Pod-Name 헤더 사용 (JWT 불필요)
# ---------------------------------------------------------------------------
info "auth-gateway에 배포 등록 중..."

# ACL 사용자 목록을 JSON 배열로 변환
ACL_JSON="[]"
if [ -n "${ACL_USERS}" ]; then
    ACL_JSON=$(echo "${ACL_USERS}" | python3 -c "
import sys, json
users = [u.strip() for u in sys.stdin.read().strip().split(',') if u.strip()]
print(json.dumps(users))
" 2>/dev/null) || ACL_JSON="[]"
fi

DEPLOY_PAYLOAD=$(python3 -c "
import json
payload = {
    'app_name': '${APP_NAME}',
    'version': '${VERSION_TAG}',
    'acl_usernames': ${ACL_JSON}
}
print(json.dumps(payload))
" 2>/dev/null)

HTTP_CODE=$(curl -sf -o /tmp/deploy-response.json -w "%{http_code}" \
    -X POST "${AUTH_GATEWAY_URL}/api/v1/apps/deploy" \
    -H "X-Pod-Name: ${POD_NAME}" \
    -H "Content-Type: application/json" \
    -d "${DEPLOY_PAYLOAD}" \
    --max-time 60 2>/dev/null) || HTTP_CODE="000"

# ---------------------------------------------------------------------------
# 5) 결과 출력
# ---------------------------------------------------------------------------
echo ""

if [ "${HTTP_CODE}" = "200" ] || [ "${HTTP_CODE}" = "201" ]; then
    # 응답에서 URL 추출
    APP_URL=$(python3 -c "
import json
try:
    d = json.load(open('/tmp/deploy-response.json'))
    print(d.get('app_url', ''))
except:
    print('')
" 2>/dev/null) || APP_URL=""

    STATUS=$(python3 -c "
import json
try:
    d = json.load(open('/tmp/deploy-response.json'))
    print(d.get('status', 'unknown'))
except:
    print('unknown')
" 2>/dev/null) || STATUS="unknown"

    echo "============================================"
    success "배포 완료!"
    echo "============================================"
    echo ""
    info "  앱:     ${APP_NAME}"
    info "  버전:   ${VERSION_TAG}"
    info "  상태:   ${STATUS}"
    [ -n "${APP_URL}" ] && info "  URL:    ${APP_URL}"
    [ -n "${ACL_USERS}" ] && info "  ACL:    ${ACL_USERS}"
    echo ""
    info "  롤백:   deploy ${APP_NAME} --rollback <버전>"
    info "  삭제:   deploy ${APP_NAME} --undeploy"
    info "  버전:   ls ~/deployed/${APP_NAME}/"
    echo ""
else
    echo "============================================"
    error "배포 실패 (HTTP ${HTTP_CODE})"
    echo "============================================"
    echo ""

    if [ "${HTTP_CODE}" = "000" ]; then
        error "auth-gateway에 연결할 수 없습니다."
        error "URL: ${AUTH_GATEWAY_URL}"
    elif [ "${HTTP_CODE}" = "403" ]; then
        error "배포 권한이 없습니다."
        error "관리자에게 can_deploy_apps 권한을 요청하세요."
    elif [ "${HTTP_CODE}" = "409" ]; then
        warn "이미 동일한 이름의 앱이 배포되어 있습니다."
        warn "재배포하려면: deploy ${APP_NAME}"
        warn "삭제 후 재배포하려면: deploy ${APP_NAME} --undeploy && deploy ${APP_NAME}"
    else
        error "서버 응답:"
        [ -f /tmp/deploy-response.json ] && cat /tmp/deploy-response.json >&2
    fi
    echo ""
    exit 1
fi

# 임시 파일 정리
rm -f /tmp/deploy-response.json
