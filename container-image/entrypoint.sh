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
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# CLAUDE.md에 사용자 프로필 섹션 추가
cat >> /home/node/CLAUDE.md << USERPROFILE

## 현재 사용자 정보

- **사번**: ${USER_ID}
- **이름**: ${USER_DISPLAY_NAME}
USERPROFILE

# 직책/부서 정보가 있으면 추가
if [ -n "${USER_POSITION}" ]; then
    echo "- **직책**: ${USER_POSITION}" >> /home/node/CLAUDE.md
fi
if [ -n "${USER_DEPARTMENT}" ]; then
    echo "- **부서**: ${USER_DEPARTMENT}" >> /home/node/CLAUDE.md
fi

cat >> /home/node/CLAUDE.md << 'USERNOTE'

이 사용자에게 한국어로 응답하세요. 사용자의 이름과 직책을 인지하고 적절한 존칭을 사용하세요.
USERNOTE

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

# ---------------------------------------------------------------------------
# 5) 환영 메시지
# ---------------------------------------------------------------------------
cat >> /home/node/.bashrc << BASHRC

# Claude Code Terminal 환영 메시지
echo ""
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║  Claude Code Terminal — ${USER_DISPLAY_NAME} 님  "
echo "  ╠══════════════════════════════════════════════════╣"
echo "  ║  claude         - Claude Code 시작               ║"
echo "  ║  /report        - 보고서 생성                     ║"
echo "  ║  /excel         - 엑셀 파일 생성                  ║"
echo "  ║  psql           - DB 접속                         ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo ""

cd ~
BASHRC

# ---------------------------------------------------------------------------
# 6) 파일 다운로드 서버 (port 8080)
#    /workspace/exports/ 와 /workspace/reports/ 를 브라우저에서 다운로드 가능
# ---------------------------------------------------------------------------
DOWNLOAD_PORT="${DOWNLOAD_PORT:-8080}"

python3 -m http.server ${DOWNLOAD_PORT} --directory /home/node/workspace &
echo "File server started on port ${DOWNLOAD_PORT}"

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
    bash -l
