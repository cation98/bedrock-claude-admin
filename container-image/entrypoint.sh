#!/bin/bash
# =============================================================================
# Claude Code Terminal - Entrypoint Script
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# 1) AWS 자격증명 확인
# ---------------------------------------------------------------------------
if [ -z "${AWS_ACCESS_KEY_ID:-}" ] && [ -z "${AWS_PROFILE:-}" ] && [ -z "${AWS_ROLE_ARN:-}" ]; then
    echo "⚠️  WARNING: No AWS credentials configured."
fi

# ---------------------------------------------------------------------------
# T20: Bedrock AG HTTP proxy 라우팅 설정
#
#   ANTHROPIC_BASE_URL이 설정된 경우 (Auth Gateway 주입):
#   1. SECURE_POD_TOKEN으로 pod-token-exchange 호출 → JWT 획득
#   2. ANTHROPIC_API_KEY = 획득한 JWT (Bearer 토큰으로 /v1/messages 인증)
#   3. CLAUDE_CODE_USE_BEDROCK unset (AWS SDK 직접 호출 비활성화)
#
#   portal.html은 pod-token-exchange를 호출하지 않음 (SSO 쿠키 사용).
#   Pod 내부 교환과 충돌 없음.
#
#   주의: JWT access_token TTL = 15분. 장시간 세션에서는 refresh 필요.
#   Phase 1에서 background token refresh daemon 추가 예정.
# ---------------------------------------------------------------------------
if [ -n "${ANTHROPIC_BASE_URL:-}" ] && [ -n "${SECURE_POD_TOKEN:-}" ] && [ -n "${AUTH_GATEWAY_URL:-}" ]; then
    # Pod 이름 결정 (k8s_service.py 생성 규칙과 동일)
    _AG_POD_NAME="claude-terminal-$(echo "${USER_ID:-unknown}" | tr '[:upper:]' '[:lower:]')"

    # pod-token-exchange 호출 → JWT 획득
    _JWT_RESPONSE=$(curl -sf -X POST \
        "${AUTH_GATEWAY_URL}/auth/pod-token-exchange" \
        -H "Content-Type: application/json" \
        -d "{\"pod_token\":\"${SECURE_POD_TOKEN}\",\"pod_name\":\"${_AG_POD_NAME}\"}" \
        --max-time 10 2>/dev/null || echo "")

    if [ -n "${_JWT_RESPONSE}" ]; then
        _ACCESS_TOKEN=$(echo "${_JWT_RESPONSE}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")
        if [ -n "${_ACCESS_TOKEN}" ]; then
            export ANTHROPIC_API_KEY="${_ACCESS_TOKEN}"
            unset CLAUDE_CODE_USE_BEDROCK 2>/dev/null || true
            echo "  Proxy:    ${ANTHROPIC_BASE_URL} (Bedrock AG T20, JWT issued)"
        else
            echo "  Proxy:    JWT 파싱 실패 — Bedrock 직접 경로 유지"
        fi
    else
        echo "  Proxy:    pod-token-exchange 실패 — Bedrock 직접 경로 유지"
    fi

    unset _JWT_RESPONSE _ACCESS_TOKEN _AG_POD_NAME
fi

# ---------------------------------------------------------------------------
# 2) 사용자 프로필을 CLAUDE.md에 주입
#    Auth Gateway가 Pod 생성 시 환경변수로 사용자 정보 전달
# ---------------------------------------------------------------------------
USER_ID="${USER_ID:-unknown}"
USER_DISPLAY_NAME="${USER_DISPLAY_NAME:-${USER_ID}}"
# TODO: 향후 SSO 확장 시 직책/부서 정보 추가
USER_POSITION="${USER_POSITION:-}"
USER_DEPARTMENT="${USER_DEPARTMENT:-}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Claude Code Terminal"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  User:     ${USER_DISPLAY_NAME} (${USER_ID})"
echo "  Bedrock:  ${CLAUDE_CODE_USE_BEDROCK:-not set}"
echo "  Region:   ${AWS_REGION:-not set}"
echo "  Model:    ${ANTHROPIC_DEFAULT_SONNET_MODEL:-default}"
echo "  Security: ${SECURITY_LEVEL:-standard}"
DB_LIST=""
[ -n "${DATABASE_URL:-}" ] && DB_LIST="${DB_LIST}Safety "
[ -n "${TANGO_DB_PASSWORD:-}" ] && DB_LIST="${DB_LIST}TANGO "
[ -n "${DOCULOG_DB_PASSWORD:-}" ] && DB_LIST="${DB_LIST}Docu-Log "
echo "  DB:       ${DB_LIST:-none}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ---------------------------------------------------------------------------
# 2a) CLAUDE.md 동적 생성 (보안 등급 기반)
#     sections 디렉토리의 파일을 조합하여 CLAUDE.md를 생성
#     SECURITY_LEVEL에 따라 DB 섹션 포함 여부 결정
# ---------------------------------------------------------------------------
SECURITY_LEVEL="${SECURITY_LEVEL:-standard}"
SECTIONS_DIR="/home/node/.claude/claude-md-sections"
CLAUDE_MD="/home/node/.claude/CLAUDE.md"

if [ -d "${SECTIONS_DIR}" ]; then
    # Start with header (미허용 DB 참조 제거)
    HEADER_CONTENT=$(cat "${SECTIONS_DIR}/00-header.md")
    # 허용 DB만 표시하도록 헤더의 DB 목록 동적 생성
    ALLOWED_DBS="psql \$DATABASE_URL"
    [ -n "${TANGO_DB_PASSWORD:-}" ] && ALLOWED_DBS="psql-tango, ${ALLOWED_DBS}"
    [ -n "${DOCULOG_DB_PASSWORD:-}" ] && ALLOWED_DBS="psql-doculog, ${ALLOWED_DBS}"
    echo "$HEADER_CONTENT" | sed "s|psql-tango, psql-doculog, psql \$DATABASE_URL|${ALLOWED_DBS}|g" > "${CLAUDE_MD}"

    # Security rules with level injection
    sed "s/{SECURITY_LEVEL}/${SECURITY_LEVEL}/g" "${SECTIONS_DIR}/10-security-rules.md" >> "${CLAUDE_MD}"

    # DB sections: conditional on environment variables
    if [ "${SECURITY_LEVEL}" != "basic" ]; then
        # DB 공통 규칙 (미허용 DB 행 제거)
        echo "" >> "${CLAUDE_MD}"
        DB_RULES=$(cat "${SECTIONS_DIR}/50-db-common-rules.md")
        [ -z "${DOCULOG_DB_PASSWORD:-}" ] && DB_RULES=$(echo "$DB_RULES" | grep -v "Docu-Log\|doculog")
        [ -z "${TANGO_DB_PASSWORD:-}" ] && DB_RULES=$(echo "$DB_RULES" | grep -v "TANGO\|tango")
        [ -z "${DATABASE_URL:-}" ] && DB_RULES=$(echo "$DB_RULES" | grep -v "Safety")
        echo "$DB_RULES" >> "${CLAUDE_MD}"

        if [ -n "${DATABASE_URL:-}" ]; then
            echo "" >> "${CLAUDE_MD}"
            cat "${SECTIONS_DIR}/30-safety-db.md" >> "${CLAUDE_MD}"
        fi

        if [ -n "${TANGO_DB_PASSWORD:-}" ] || grep -q "claude_readonly" /home/node/.pgpass 2>/dev/null; then
            echo "" >> "${CLAUDE_MD}"
            cat "${SECTIONS_DIR}/20-tango-db.md" >> "${CLAUDE_MD}"
            echo "" >> "${CLAUDE_MD}"
            cat "${SECTIONS_DIR}/25-opark-db.md" >> "${CLAUDE_MD}"
        fi

        if [ -n "${DOCULOG_DB_PASSWORD:-}" ] || grep -q "doculog_reader" /home/node/.pgpass 2>/dev/null; then
            echo "" >> "${CLAUDE_MD}"
            cat "${SECTIONS_DIR}/35-doculog-db.md" >> "${CLAUDE_MD}"
        fi

        # 키워드 매핑: 미허용 DB 행 제거
        echo "" >> "${CLAUDE_MD}"
        KEYWORD_CONTENT=$(cat "${SECTIONS_DIR}/40-keyword-mapping.md")
        if [ -z "${DOCULOG_DB_PASSWORD:-}" ]; then
            KEYWORD_CONTENT=$(echo "$KEYWORD_CONTENT" | grep -v "Docu-Log")
        fi
        if [ -z "${DATABASE_URL:-}" ]; then
            KEYWORD_CONTENT=$(echo "$KEYWORD_CONTENT" | grep -v "Safety DB")
        fi
        if [ -z "${TANGO_DB_PASSWORD:-}" ]; then
            KEYWORD_CONTENT=$(echo "$KEYWORD_CONTENT" | grep -v "TANGO DB")
        fi
        echo "$KEYWORD_CONTENT" >> "${CLAUDE_MD}"
    fi

    # 대용량 파일 처리 규칙 (항상 포함)
    if [ -f "${SECTIONS_DIR}/45-large-file-rules.md" ]; then
        echo "" >> "${CLAUDE_MD}"
        cat "${SECTIONS_DIR}/45-large-file-rules.md" >> "${CLAUDE_MD}"
    fi

    # Always include web terminal, tools, webapp sections
    echo "" >> "${CLAUDE_MD}"
    # 웹 터미널/도구 섹션 (미허용 DB 도구 제거)
    WEB_CONTENT=$(cat "${SECTIONS_DIR}/60-web-terminal.md")
    [ -z "${DOCULOG_DB_PASSWORD:-}" ] && WEB_CONTENT=$(echo "$WEB_CONTENT" | grep -v "doculog\|Docu-Log")
    [ -z "${TANGO_DB_PASSWORD:-}" ] && WEB_CONTENT=$(echo "$WEB_CONTENT" | grep -v "psql-tango\|TANGO")
    echo "$WEB_CONTENT" >> "${CLAUDE_MD}"

    echo "  CLAUDE.md 생성 완료 (level=${SECURITY_LEVEL})"
else
    echo "  WARNING: sections dir not found, using fallback CLAUDE.md"
fi

# 글로벌 CLAUDE.md (~/.claude/CLAUDE.md)에 사용자 프로필 추가
cat >> /home/node/.claude/CLAUDE.md << USERPROFILE

## 현재 사용자 정보

- **사번**: ${USER_ID}
- **이름**: ${USER_DISPLAY_NAME}
USERPROFILE

if [ -n "${USER_POSITION}" ]; then
    echo "- **직책**: ${USER_POSITION}" >> /home/node/.claude/CLAUDE.md
fi
if [ -n "${USER_DEPARTMENT}" ]; then
    echo "- **부서**: ${USER_DEPARTMENT}" >> /home/node/.claude/CLAUDE.md
fi

# ---------------------------------------------------------------------------
# 3) Git 설정
# ---------------------------------------------------------------------------
git config --global user.name "${USER_DISPLAY_NAME}"
git config --global user.email "${USER_ID}@skons.net"

# ---------------------------------------------------------------------------
# 4) 작업 디렉토리 준비
# ---------------------------------------------------------------------------
mkdir -p /home/node/.claude
mkdir -p /home/node/workspace/exports
mkdir -p /home/node/workspace/reports
mkdir -p /home/node/workspace/uploads

# 공유 데이터 디렉토리 준비
mkdir -p /home/node/workspace/shared-data
mkdir -p /home/node/workspace/team

# ---------------------------------------------------------------------------
# 4a) 이전 대화 자동 복원 (EFS 백업 → ~/.claude/)
# ---------------------------------------------------------------------------
if [ -d /home/node/workspace/.claude-backup/projects ]; then
    cp -r /home/node/workspace/.claude-backup/projects/ /home/node/.claude/projects/ 2>/dev/null
    cp /home/node/workspace/.claude-backup/history.jsonl /home/node/.claude/history.jsonl 2>/dev/null
    # 세션 메타데이터 복원 — /resume 목록에 이전 세션 표시
    cp -r /home/node/workspace/.claude-backup/sessions/ /home/node/.claude/sessions/ 2>/dev/null || true
    echo "  이전 대화 복원 완료"
fi
# .serena 프로젝트 메모리 복원
if [ -d /home/node/workspace/.serena-backup ]; then
    cp -r /home/node/workspace/.serena-backup/ /home/node/.serena/ 2>/dev/null
    echo "  Serena 프로젝트 메모리 복원 완료"
fi

# ---------------------------------------------------------------------------
# 4b) DB .pgpass 설정 — 보안 정책에 따라 허용된 DB만 자격증명 설정
#     환경변수가 없으면 해당 DB 항목을 추가하지 않음
# ---------------------------------------------------------------------------
> /home/node/.pgpass

if [ -n "${TANGO_DB_PASSWORD:-}" ]; then
    echo "aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com:5432:postgres:claude_readonly:${TANGO_DB_PASSWORD}" >> /home/node/.pgpass
    unset TANGO_DB_PASSWORD
fi

if [ -n "${DOCULOG_DB_PASSWORD:-}" ]; then
    echo "aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com:5432:doculog:doculog_reader:${DOCULOG_DB_PASSWORD}" >> /home/node/.pgpass
    unset DOCULOG_DB_PASSWORD
fi

chmod 600 /home/node/.pgpass

# ---------------------------------------------------------------------------
# 5) psql 스크립트 생성 — 보안 정책에 따라 실제 스크립트 또는 접근거부 스텁
# ---------------------------------------------------------------------------
mkdir -p /home/node/.local/bin

# TANGO_DATABASE_URL이 있으면 TANGO DB 접근 허용됨
if [ -n "${TANGO_DATABASE_URL:-}" ]; then
    cat > /home/node/.local/bin/psql-tango << 'DBSCRIPT'
#!/bin/sh
exec psql "host=aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com dbname=postgres user=claude_readonly sslmode=require" "$@"
DBSCRIPT
else
    cat > /home/node/.local/bin/psql-tango << 'DBSCRIPT'
#!/bin/sh
echo "TANGO DB 접근 권한이 없습니다. 관리자에게 문의하세요." >&2
exit 1
DBSCRIPT
fi
chmod +x /home/node/.local/bin/psql-tango

# DOCULOG_DB_PASSWORD가 .pgpass에 기록되었으면 접근 허용 (이미 unset됨 → .pgpass 존재 여부로 판별)
if grep -q "doculog_reader" /home/node/.pgpass 2>/dev/null; then
    cat > /home/node/.local/bin/psql-doculog << 'DBSCRIPT'
#!/bin/sh
exec psql "host=aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com dbname=doculog user=doculog_reader sslmode=require" "$@"
DBSCRIPT
else
    cat > /home/node/.local/bin/psql-doculog << 'DBSCRIPT'
#!/bin/sh
echo "Docu-Log DB 접근 권한이 없습니다. 관리자에게 문의하세요." >&2
exit 1
DBSCRIPT
fi
chmod +x /home/node/.local/bin/psql-doculog

# backup-chat / restore-chat 스크립트
cat > /home/node/.local/bin/backup-chat << 'BSCRIPT'
#!/bin/bash
mkdir -p /home/node/workspace/.claude-backup
cp -r /home/node/.claude/projects/ /home/node/workspace/.claude-backup/ 2>/dev/null
cp /home/node/.claude/history.jsonl /home/node/workspace/.claude-backup/ 2>/dev/null
# 세션 메타데이터 백업 — /resume 목록 복원에 필요
cp -r /home/node/.claude/sessions/ /home/node/workspace/.claude-backup/ 2>/dev/null
# Serena 프로젝트 메모리 백업
if [ -d /home/node/.serena ]; then
    cp -r /home/node/.serena/ /home/node/workspace/.serena-backup/ 2>/dev/null
    echo "Serena 메모리 백업 완료"
fi
echo "대화 백업 완료: ~/workspace/.claude-backup/"
BSCRIPT
chmod +x /home/node/.local/bin/backup-chat

cat > /home/node/.local/bin/restore-chat << 'RSCRIPT'
#!/bin/bash
if [ -d /home/node/workspace/.claude-backup/projects ]; then
    cp -r /home/node/workspace/.claude-backup/projects/ /home/node/.claude/projects/
    cp /home/node/workspace/.claude-backup/history.jsonl /home/node/.claude/history.jsonl 2>/dev/null
    cp -r /home/node/workspace/.claude-backup/sessions/ /home/node/.claude/sessions/ 2>/dev/null
    echo "대화 복원 완료."
else
    echo "대화 백업이 없습니다."
fi
# Serena 복원
if [ -d /home/node/workspace/.serena-backup ]; then
    cp -r /home/node/workspace/.serena-backup/ /home/node/.serena/ 2>/dev/null
    echo "Serena 메모리 복원 완료."
else
    echo "Serena 백업이 없습니다."
fi
RSCRIPT
chmod +x /home/node/.local/bin/restore-chat

# deploy / undeploy / deploy-rollback 스크립트 — 웹앱 배포 CLI
# deploy.sh를 /home/node/.local/bin/deploy로 설치하고 편의 별칭 생성
if [ -f /usr/local/bin/deploy.sh ]; then
    cp /usr/local/bin/deploy.sh /home/node/.local/bin/deploy
    chmod +x /home/node/.local/bin/deploy

    # undeploy: deploy <app> --undeploy 의 단축 명령
    cat > /home/node/.local/bin/undeploy << 'UDSCRIPT'
#!/bin/bash
if [ -z "${1:-}" ]; then
    echo "사용법: undeploy <앱이름>"
    echo "  배포된 앱을 삭제합니다. 소스 코드(~/apps/)는 유지됩니다."
    exit 1
fi
exec deploy "$1" --undeploy
UDSCRIPT
    chmod +x /home/node/.local/bin/undeploy

    # deploy-rollback: deploy <app> --rollback <version> 의 단축 명령
    cat > /home/node/.local/bin/deploy-rollback << 'RBSCRIPT'
#!/bin/bash
if [ -z "${1:-}" ] || [ -z "${2:-}" ]; then
    echo "사용법: deploy-rollback <앱이름> <버전>"
    echo "  예시: deploy-rollback my-app v-20260401-1430"
    echo ""
    echo "  배포 가능한 버전 확인: ls ~/deployed/<앱이름>/"
    exit 1
fi
exec deploy "$1" --rollback "$2"
RBSCRIPT
    chmod +x /home/node/.local/bin/deploy-rollback

    # 앱 개발 디렉토리 사전 생성
    mkdir -p /home/node/apps
fi

# ---------------------------------------------------------------------------
# 4b) 공유 데이터 심링크 동기화 (60초 주기)
#     .efs-users/ (readOnly EFS 마운트)에서 공유된 데이터셋을
#     ~/workspace/team/{owner}/{name} 심링크로 생성/삭제.
#     공유 추가/해제 시 Pod 재시작 없이 실시간 반영.
# ---------------------------------------------------------------------------
if [ -n "${AUTH_GATEWAY_URL:-}" ] && [ -d "/home/node/.efs-users" ]; then
    (bash /usr/local/bin/share-sync.sh) &
    echo "  공유 동기화 시작 (60초 주기)"
fi

# ---------------------------------------------------------------------------
# 4c) 유휴 감지 heartbeat (5분마다) — 브라우저 접속 중일 때만 전송
#     ttyd 포트(7681=0x1E01)에 ESTABLISHED 연결이 있을 때만 heartbeat 전송.
#     브라우저가 닫히면 heartbeat 중단 → 60분 후 idle cleanup이 Pod 종료.
# ---------------------------------------------------------------------------
if [ -n "${AUTH_GATEWAY_URL:-}" ]; then
    POD_NAME="claude-terminal-$(echo ${USER_ID} | tr '[:upper:]' '[:lower:]')"
    (while true; do
        sleep 300
        # 포트 7681(0x1E01)에 ESTABLISHED(01) 연결 확인
        ACTIVE=$(awk '$2 ~ /:1E01$/ && $4 == "01"' /proc/net/tcp 2>/dev/null | wc -l)
        if [ "$ACTIVE" -gt 0 ]; then
            curl -sf -X POST "${AUTH_GATEWAY_URL}/api/v1/sessions/internal-heartbeat" \
                -H "X-Pod-Name: ${POD_NAME}" \
                --max-time 5 2>/dev/null || true
        fi
    done) &
fi

# ---------------------------------------------------------------------------
# 4c-1) 유휴 종료 경고 감지 (10초 주기)
#       idle_cleanup_service가 /tmp/.idle-warning 파일 생성 시
#       터미널에 경고 메시지 출력 + 파일 삭제
# ---------------------------------------------------------------------------
(while true; do
    sleep 10
    if [ -f /tmp/.idle-warning ]; then
        MINUTES=$(python3 -c "import json; print(json.load(open('/tmp/.idle-warning')).get('minutes_left',5))" 2>/dev/null || echo 5)
        # 모든 터미널 pts에 경고 메시지 전송
        for pts in /dev/pts/[0-9]*; do
            echo "" > "$pts" 2>/dev/null
            echo "  ⚠ [유휴 경고] ${MINUTES}분 후 세션이 종료됩니다." > "$pts" 2>/dev/null
            echo "  아무 키를 입력하면 유지됩니다." > "$pts" 2>/dev/null
            echo "" > "$pts" 2>/dev/null
        done
        rm -f /tmp/.idle-warning
    fi
done) &

# ---------------------------------------------------------------------------
# 4c-2) 메모리 사용량 모니터 (15초마다)
#       cgroup v2 memory.current / memory.max 로 사용률 계산.
#       80% 초과 → 경고, 90% 초과 → 긴급 경고 + Claude AI 알림 파일 생성.
#       OOMKilled 방지를 위해 사전 경고.
# ---------------------------------------------------------------------------
(while true; do
    sleep 15
    # cgroup v2 메모리 정보 읽기
    MEM_CURRENT=$(cat /sys/fs/cgroup/memory.current 2>/dev/null || echo 0)
    MEM_MAX=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo "max")
    if [ "$MEM_MAX" = "max" ] || [ "$MEM_MAX" = "0" ]; then
        sleep 45
        continue
    fi
    PCT=$((MEM_CURRENT * 100 / MEM_MAX))
    MEM_MB=$((MEM_CURRENT / 1048576))
    MAX_MB=$((MEM_MAX / 1048576))

    if [ "$PCT" -ge 90 ]; then
        # 긴급 경고: 터미널 + Claude AI 알림 파일
        for pts in /dev/pts/[0-9]*; do
            echo "" > "$pts" 2>/dev/null
            echo "  🚨 [긴급 메모리 경고] ${MEM_MB}MB / ${MAX_MB}MB (${PCT}%) — OOM 위험!" > "$pts" 2>/dev/null
            echo "  대용량 작업을 즉시 중단하고, 불필요한 프로세스를 종료하세요." > "$pts" 2>/dev/null
            echo "  (kill PID 또는 Ctrl+C로 현재 작업 중단)" > "$pts" 2>/dev/null
            echo "" > "$pts" 2>/dev/null
        done
        # Claude AI가 감지할 수 있는 알림 파일
        echo "{\"level\":\"critical\",\"memory_pct\":${PCT},\"memory_mb\":${MEM_MB},\"max_mb\":${MAX_MB}}" > /tmp/.memory-warning
    elif [ "$PCT" -ge 80 ]; then
        # 주의 경고: 터미널에만 표시
        for pts in /dev/pts/[0-9]*; do
            echo "" > "$pts" 2>/dev/null
            echo "  ⚠ [메모리 주의] ${MEM_MB}MB / ${MAX_MB}MB (${PCT}%) — 대용량 작업 시 주의" > "$pts" 2>/dev/null
            echo "" > "$pts" 2>/dev/null
        done
        rm -f /tmp/.memory-warning
    else
        rm -f /tmp/.memory-warning
    fi
done) &

# ---------------------------------------------------------------------------
# 4c-3) 대화이력 주기적 자동 백업 (30분마다) — TTL 만료/크래시 시에도 보존
# ---------------------------------------------------------------------------
(while true; do sleep 1800; backup-chat 2>/dev/null; done) &

export PATH="/home/node/.local/bin:$PATH"

# ---------------------------------------------------------------------------
# 5b) Superpowers 플러그인 설치 (CLI 등록, 1회만)
# ---------------------------------------------------------------------------
if [ ! -f /home/node/workspace/.plugins-installed ]; then
    echo "  플러그인 설치 중..."
    claude plugin install superpowers 2>/dev/null || true
    touch /home/node/workspace/.plugins-installed
    echo "  플러그인 설치 완료"
fi

# ---------------------------------------------------------------------------
# 6) 환영 메시지
# ---------------------------------------------------------------------------
if [ -n "${DATABASE_URL:-}" ]; then
    cat > /home/node/.local/bin/psql-safety << 'DBSCRIPT'
#!/bin/sh
exec psql "$DATABASE_URL" "$@"
DBSCRIPT
else
    cat > /home/node/.local/bin/psql-safety << 'DBSCRIPT'
#!/bin/sh
echo "Safety DB 접근 권한이 없습니다. 관리자에게 문의하세요." >&2
exit 1
DBSCRIPT
fi
chmod +x /home/node/.local/bin/psql-safety
export PATH="/home/node/.local/bin:$PATH"
echo 'export PATH="/home/node/.local/bin:$PATH"' >> /home/node/.bashrc

# 사용 가능한 DB 명령 목록 구성
AVAIL_CMDS="claude"
[ -n "${DATABASE_URL:-}" ] && AVAIL_CMDS="${AVAIL_CMDS} / psql-safety"
[ -n "${TANGO_DATABASE_URL:-}" ] && AVAIL_CMDS="${AVAIL_CMDS} / psql-tango"
grep -q "doculog_reader" /home/node/.pgpass 2>/dev/null && AVAIL_CMDS="${AVAIL_CMDS} / psql-doculog"
AVAIL_CMDS="${AVAIL_CMDS} / /report / /excel"
[ -f /home/node/.local/bin/deploy ] && AVAIL_CMDS="${AVAIL_CMDS} / deploy"

# 공유 데이터 안내 메시지 구성
SHARED_DB_MSG=""
SHARED_COUNT=$(find /home/node/workspace/team -name "*.sqlite" 2>/dev/null | wc -l | tr -d ' ')
MY_DB_COUNT=$(find /home/node/workspace/shared-data -name "*.sqlite" 2>/dev/null | wc -l | tr -d ' ')
if [ "$MY_DB_COUNT" -gt 0 ] || [ "$SHARED_COUNT" -gt 0 ]; then
    SHARED_DB_MSG="  DB: 내 데이터 ${MY_DB_COUNT}개"
    [ "$SHARED_COUNT" -gt 0 ] && SHARED_DB_MSG="${SHARED_DB_MSG}, 공유받은 데이터 ${SHARED_COUNT}개"
fi

# 환영 메시지 (unquoted: USER_DISPLAY_NAME, AVAIL_CMDS 확장)
cat >> /home/node/.bashrc << WELCOME
echo ""
echo "  Claude Code Terminal — ${USER_DISPLAY_NAME} 님"
echo "  ${AVAIL_CMDS}"
WELCOME

# 공유 데이터 안내 (있는 경우에만)
if [ -n "$SHARED_DB_MSG" ]; then
    cat >> /home/node/.bashrc << DBWELCOME
echo "  ${SHARED_DB_MSG}"
echo "  /db 명령으로 데이터베이스 조회 가능"
DBWELCOME
fi

cat >> /home/node/.bashrc << WELCOMEEND
echo ""
cd ~
WELCOMEEND

# Claude Code 자동 시작 — .bashrc 자동시작 제거 (보안: claude-wrapper로 대체)
# ttyd가 claude-wrapper를 직접 실행하므로 .bashrc 자동시작 불필요

# ---------------------------------------------------------------------------
# 6) 파일 업로드/다운로드 서버 (port 8080)
#    업로드: 브라우저에서 드래그&드롭으로 파일 업로드 → /workspace/uploads/
#    다운로드: /workspace/ 하위 모든 파일 브라우저에서 다운로드 가능
# ---------------------------------------------------------------------------
FILE_SERVER_PORT="${FILE_SERVER_PORT:-8080}"

python3 /usr/local/bin/fileserver.py --port "${FILE_SERVER_PORT}" --dir /home/node/workspace &
echo "File server (upload+download) started on port ${FILE_SERVER_PORT}"

# ---------------------------------------------------------------------------
# 8) claude-wrapper 생성 (보안: Claude 종료 시 쉘 접근 차단)
#    ttyd가 이 wrapper를 직접 실행하여, Claude Code 종료 후
#    사용자가 bash 쉘에 접근하지 못하도록 함
# ---------------------------------------------------------------------------
cat > /home/node/.local/bin/claude-wrapper << 'WRAPPER'
#!/bin/bash
# Source profile for PATH and env vars
export PATH="/home/node/.local/bin:$PATH"

echo ""
echo "  Claude Code를 시작합니다..."
echo ""

# Run Claude Code
# --continue: 이전 대화가 있으면 이어서 시작. 없으면(신규 사용자) 새 대화 시작.
CLAUDE_ARGS="--dangerously-skip-permissions"
if [ -d "/home/node/.claude/projects" ] && [ "$(find /home/node/.claude/projects -name '*.jsonl' 2>/dev/null | head -1)" ]; then
    CLAUDE_ARGS="${CLAUDE_ARGS} --continue"
fi
claude ${CLAUDE_ARGS} \
    --append-system-prompt "항상 한국어로 응답하세요. 사용자의 이름을 인지하고 존칭을 사용하세요."

# Claude exited — backup conversations to EFS
echo ""
echo "  대화 내용을 백업 중..."
backup-chat 2>/dev/null

# Show termination message and block further input
# 'read' will block forever — user sees the message but cannot execute commands
echo ""
echo "============================================"
echo "  세션이 종료되었습니다."
echo "  대화 내용은 자동 백업되었습니다."
echo "  브라우저를 닫아주세요."
echo "============================================"
echo ""

# Block forever — prevents shell access after Claude exits
# If the user closes the browser tab, ttyd terminates naturally
read -r
WRAPPER
chmod +x /home/node/.local/bin/claude-wrapper

# ---------------------------------------------------------------------------
# 9) 보안 파일 읽기전용 설정 (entrypoint 수정 완료 후)
# ---------------------------------------------------------------------------
chmod 444 /home/node/.claude/CLAUDE.md /home/node/.claude/settings.json 2>/dev/null

# ---------------------------------------------------------------------------
# 10) ttyd 시작 — claude-wrapper를 직접 실행 (bash -l 대신)
#    보안: Claude 종료 후 bash 쉘에 접근 불가
# ---------------------------------------------------------------------------
TTYD_PORT="${TTYD_PORT:-7681}"
TTYD_BASE_PATH="${TTYD_BASE_PATH:-/}"

echo "Starting ttyd on port ${TTYD_PORT}..."

exec ttyd \
    --writable \
    --port "${TTYD_PORT}" \
    --base-path "${TTYD_BASE_PATH}" \
    --ping-interval 30 \
    --max-clients 1 \
    --client-option reconnect=3 \
    --client-option titleFixed="Claude Code Terminal" \
    --client-option rendererType=dom \
    --client-option disableLeaveAlert=true \
    --client-option allowProposedApi=true \
    /home/node/.local/bin/claude-wrapper
