#!/bin/bash
# =============================================================================
# App Runtime Entrypoint
# 앱 타입을 자동 감지하여 적절한 방식으로 시작합니다.
#
# 감지 우선순위:
#   1. start.sh        -- 커스텀 시작 스크립트 (최우선)
#   2. main.py         -- FastAPI uvicorn (main:app)
#   3. app.py          -- FastAPI uvicorn (app:app)
#   4. package.json    -- npm start
#   5. server.js       -- node server.js
#   6. index.js        -- node index.js
#
# 환경변수:
#   APP_NAME     -- 앱 이름 (auth-gateway가 설정)
#   APP_VERSION  -- 배포 버전 (auth-gateway가 설정)
#   APP_OWNER    -- 배포자 사번
# =============================================================================

set -euo pipefail
cd /app

echo "============================================"
echo "  App Runtime"
echo "============================================"
echo "  App:     ${APP_NAME:-unknown}"
echo "  Version: ${APP_VERSION:-dev}"
echo "  Owner:   ${APP_OWNER:-unknown}"
echo "============================================"

# ---------------------------------------------------------------------------
# Python 의존성 설치 (requirements.txt가 있는 경우)
#   --break-system-packages: bookworm-slim에서 venv 없이 설치 허용
#   실패해도 계속 진행 (사전 설치된 패키지로 충분할 수 있음)
# ---------------------------------------------------------------------------
if [ -f requirements.txt ]; then
    echo "[runtime] requirements.txt 발견 -- Python 의존성 설치 중..."
    pip3 install --break-system-packages -r requirements.txt 2>/dev/null || true
    echo "[runtime] Python 의존성 설치 완료"
fi

# ---------------------------------------------------------------------------
# Node.js 의존성 설치 (package.json이 있고 node_modules가 없는 경우)
#   --production: devDependencies 제외하여 이미지 크기 절약
# ---------------------------------------------------------------------------
if [ -f package.json ] && [ ! -d node_modules ]; then
    echo "[runtime] package.json 발견 -- Node.js 의존성 설치 중..."
    npm install --production 2>/dev/null || true
    echo "[runtime] Node.js 의존성 설치 완료"
fi

# ---------------------------------------------------------------------------
# 앱 타입 자동 감지 및 실행
#   exec로 PID 1을 앱 프로세스에 양도 (시그널 전달, 좀비 방지)
# ---------------------------------------------------------------------------
if [ -f start.sh ]; then
    echo "[runtime] start.sh 감지 -- 커스텀 스크립트 실행"
    exec bash start.sh

elif [ -f main.py ]; then
    echo "[runtime] main.py 감지 -- uvicorn main:app 실행"
    exec python3 -m uvicorn main:app --host 0.0.0.0 --port 3000

elif [ -f app.py ]; then
    echo "[runtime] app.py 감지 -- uvicorn app:app 실행"
    exec python3 -m uvicorn app:app --host 0.0.0.0 --port 3000

elif [ -f package.json ]; then
    echo "[runtime] package.json 감지 -- npm start 실행"
    exec npm start

elif [ -f server.js ]; then
    echo "[runtime] server.js 감지 -- node server.js 실행"
    exec node server.js

elif [ -f index.js ]; then
    echo "[runtime] index.js 감지 -- node index.js 실행"
    exec node index.js

else
    echo ""
    echo "============================================"
    echo "  오류: 실행 가능한 앱을 찾을 수 없습니다"
    echo "============================================"
    echo "  /app 디렉토리에 다음 중 하나가 필요합니다:"
    echo "    - start.sh      (커스텀 시작 스크립트)"
    echo "    - main.py       (FastAPI -- uvicorn main:app)"
    echo "    - app.py        (FastAPI -- uvicorn app:app)"
    echo "    - package.json  (Node.js -- npm start)"
    echo "    - server.js     (Node.js -- node server.js)"
    echo "    - index.js      (Node.js -- node index.js)"
    echo "============================================"
    exit 1
fi
