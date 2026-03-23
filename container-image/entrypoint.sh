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
# 4b) TANGO DB .pgpass 설정 (패스워드 내 ! 특수문자 처리)
# ---------------------------------------------------------------------------
echo "aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com:5432:postgres:claude_readonly:TangoReadOnly2026" > /home/node/.pgpass
chmod 600 /home/node/.pgpass

# ---------------------------------------------------------------------------
# 5) 환영 메시지
# ---------------------------------------------------------------------------
# DB 접속 스크립트
mkdir -p /home/node/.local/bin

cat > /home/node/.local/bin/psql-tango << 'DBSCRIPT'
#!/bin/sh
export PGPASSWORD="TangoReadOnly2026"
exec psql "host=aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com dbname=postgres user=claude_readonly sslmode=require" "$@"
DBSCRIPT
chmod +x /home/node/.local/bin/psql-tango

cat > /home/node/.local/bin/psql-safety << 'DBSCRIPT'
#!/bin/sh
exec psql "$DATABASE_URL" "$@"
DBSCRIPT
chmod +x /home/node/.local/bin/psql-safety
export PATH="/home/node/.local/bin:$PATH"
echo 'export PATH="/home/node/.local/bin:$PATH"' >> /home/node/.bashrc

# 환영 메시지 (unquoted heredoc: 변수 확장 필요)
cat >> /home/node/.bashrc << BASHRC

echo ""
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║  Claude Code Terminal — ${USER_DISPLAY_NAME} 님          "
echo "  ╠══════════════════════════════════════════════════════════╣"
echo "  ║  claude         - Claude Code 시작                       ║"
echo "  ║  psql-safety    - 안전관리 DB 접속                        ║"
echo "  ║  psql-tango     - TANGO 알람 DB 접속                      ║"
echo "  ║  /report        - 보고서 생성                             ║"
echo "  ║  /excel         - 엑셀 파일 생성                          ║"
echo "  ╠══════════════════════════════════════════════════════════╣"
echo "  ║  파일 업로드/다운로드: /files/ 페이지에서 드래그&드롭       ║"
echo "  ║  업로드 경로: ~/workspace/uploads/                        ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo ""

cd ~
BASHRC

# ---------------------------------------------------------------------------
# 6) 파일 업로드/다운로드 서버 (port 8080)
#    업로드: 브라우저에서 드래그&드롭으로 파일 업로드 → /workspace/uploads/
#    다운로드: /workspace/ 하위 모든 파일 브라우저에서 다운로드 가능
# ---------------------------------------------------------------------------
FILE_SERVER_PORT="${FILE_SERVER_PORT:-8080}"

python3 /usr/local/bin/fileserver.py --port "${FILE_SERVER_PORT}" --dir /home/node/workspace &
echo "File server (upload+download) started on port ${FILE_SERVER_PORT}"

# ---------------------------------------------------------------------------
# 7) ttyd 시작
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
    bash -l
