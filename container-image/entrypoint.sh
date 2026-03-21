#!/bin/bash
# =============================================================================
# Claude Code Terminal - Entrypoint Script
#
# ttyd 웹 터미널을 시작하고, 사용자가 브라우저에서 접속하면
# Claude Code가 사전 구성된 bash 셸을 제공합니다.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# 1) AWS 자격증명 확인
#    Pod 생성 시 Auth Gateway가 STS 임시 자격증명을 환경변수로 주입합니다.
# ---------------------------------------------------------------------------
if [ -z "${AWS_ACCESS_KEY_ID:-}" ] && [ -z "${AWS_PROFILE:-}" ] && [ -z "${AWS_ROLE_ARN:-}" ]; then
    echo "⚠️  WARNING: No AWS credentials configured."
    echo "   Claude Code requires AWS credentials to access Bedrock."
    echo "   Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or AWS_PROFILE."
fi

# ---------------------------------------------------------------------------
# 2) Bedrock 설정 확인
# ---------------------------------------------------------------------------
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Claude Code Terminal"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Bedrock:  ${CLAUDE_CODE_USE_BEDROCK:-not set}"
echo "  Region:   ${AWS_REGION:-not set}"
echo "  Model:    ${ANTHROPIC_DEFAULT_SONNET_MODEL:-default}"
echo "  User:     $(whoami)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ---------------------------------------------------------------------------
# 3) Claude Code 초기 설정 건너뛰기
#    TOS 수락, 온보딩을 미리 완료 상태로 설정
# ---------------------------------------------------------------------------
mkdir -p /home/node/.claude

# 프로젝트 디렉토리에도 .claude 설정 생성
mkdir -p /home/node/workspace/sample-project/.claude

# ---------------------------------------------------------------------------
# 4) Git 기본 설정 (실습 시 커밋 가능하도록)
# ---------------------------------------------------------------------------
if [ -n "${GIT_USER_NAME:-}" ]; then
    git config --global user.name "${GIT_USER_NAME}"
fi
if [ -n "${GIT_USER_EMAIL:-}" ]; then
    git config --global user.email "${GIT_USER_EMAIL}"
fi

# ---------------------------------------------------------------------------
# 4) 환영 메시지 생성 (.bashrc에 추가)
# ---------------------------------------------------------------------------
cat >> /home/node/.bashrc << 'BASHRC'

# Claude Code Terminal 환영 메시지
echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║        Claude Code Terminal Ready             ║"
echo "  ╠══════════════════════════════════════════════╣"
echo "  ║  claude         - Claude Code 시작            ║"
echo "  ║  claude --help  - 도움말                      ║"
echo "  ║  psql           - PostgreSQL 접속              ║"
echo "  ║  aws sts get-caller-identity  - AWS 인증 확인  ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""

# 샘플 프로젝트 디렉토리로 이동
cd ~/workspace/sample-project 2>/dev/null || cd ~/workspace

BASHRC

# ---------------------------------------------------------------------------
# 5) ttyd 시작
#    --writable: 사용자 입력 허용
#    --port: 웹 터미널 포트
#    --base-path: 리버스 프록시 경로 (K8s Ingress에서 사용)
#    bash -l: 로그인 셸로 시작 (.bashrc 실행)
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
