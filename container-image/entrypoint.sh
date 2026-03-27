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
echo "  DB:       Safety + TANGO Alarm"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

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

# ---------------------------------------------------------------------------
# 4a) 이전 대화 자동 복원 (EFS 백업 → ~/.claude/)
# ---------------------------------------------------------------------------
if [ -d /home/node/workspace/.claude-backup/projects ]; then
    cp -r /home/node/workspace/.claude-backup/projects/ /home/node/.claude/projects/ 2>/dev/null
    cp /home/node/workspace/.claude-backup/history.jsonl /home/node/.claude/history.jsonl 2>/dev/null
    echo "  이전 대화 복원 완료"
fi
# .serena 프로젝트 메모리 복원
if [ -d /home/node/workspace/.serena-backup ]; then
    cp -r /home/node/workspace/.serena-backup/ /home/node/.serena/ 2>/dev/null
    echo "  Serena 프로젝트 메모리 복원 완료"
fi

# ---------------------------------------------------------------------------
# 4b) TANGO DB .pgpass 설정 (패스워드 내 ! 특수문자 처리)
# ---------------------------------------------------------------------------
echo "aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com:5432:postgres:claude_readonly:${TANGO_DB_PASSWORD}" > /home/node/.pgpass
chmod 600 /home/node/.pgpass

# 비밀번호 환경변수 제거 (보안)
unset TANGO_DB_PASSWORD

# ---------------------------------------------------------------------------
# 5) psql-tango 스크립트 생성 (TANGO DB 접속 단축 명령)
# ---------------------------------------------------------------------------
mkdir -p /home/node/.local/bin
cat > /home/node/.local/bin/psql-tango << 'DBSCRIPT'
#!/bin/sh
exec psql "host=aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com dbname=postgres user=claude_readonly sslmode=require" "$@"
DBSCRIPT
chmod +x /home/node/.local/bin/psql-tango

# backup-chat / restore-chat 스크립트
cat > /home/node/.local/bin/backup-chat << 'BSCRIPT'
#!/bin/bash
mkdir -p /home/node/workspace/.claude-backup
cp -r /home/node/.claude/projects/ /home/node/workspace/.claude-backup/ 2>/dev/null
cp /home/node/.claude/history.jsonl /home/node/workspace/.claude-backup/ 2>/dev/null
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
cat > /home/node/.local/bin/psql-safety << 'DBSCRIPT'
#!/bin/sh
exec psql "$DATABASE_URL" "$@"
DBSCRIPT
chmod +x /home/node/.local/bin/psql-safety
export PATH="/home/node/.local/bin:$PATH"
echo 'export PATH="/home/node/.local/bin:$PATH"' >> /home/node/.bashrc

# 환영 메시지 (unquoted: USER_DISPLAY_NAME 확장)
cat >> /home/node/.bashrc << WELCOME
echo ""
echo "  Claude Code Terminal — ${USER_DISPLAY_NAME} 님"
echo "  claude / psql-safety / psql-tango / /report / /excel"
echo ""
cd ~
WELCOME

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
claude --dangerously-skip-permissions \
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
