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
#   2. ANTHROPIC_AUTH_TOKEN = 획득한 JWT
#      (Claude Code가 자동으로 "Bearer " 접두사를 붙여 Authorization 헤더에 사용)
#   3. CLAUDE_CODE_USE_BEDROCK unset (AWS SDK 직접 호출 비활성화)
#
#   ANTHROPIC_AUTH_TOKEN을 쓰는 이유:
#   - ANTHROPIC_API_KEY로 export하면 Claude Code가 "Detected a custom API key"
#     승인 프롬프트를 사용자에게 띄움 → UX 저하.
#   - ANTHROPIC_AUTH_TOKEN은 OAuth/Bearer 전용 변수로 인식되어 프롬프트 없이
#     Authorization 헤더에 그대로 적용됨.
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
        _REFRESH_TOKEN=$(echo "${_JWT_RESPONSE}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('refresh_token',''))" 2>/dev/null || echo "")
        if [ -n "${_ACCESS_TOKEN}" ]; then
            export ANTHROPIC_AUTH_TOKEN="${_ACCESS_TOKEN}"
            # Token refresh daemon이 파일을 통해 새 토큰을 전달 — claude-wrapper가 이 파일을 읽음
            echo "${_ACCESS_TOKEN}" > /home/node/.claude-token
            chmod 600 /home/node/.claude-token
            if [ -n "${_REFRESH_TOKEN}" ]; then
                echo "${_REFRESH_TOKEN}" > /home/node/.claude-refresh-token
                chmod 600 /home/node/.claude-refresh-token
            fi
            # ANTHROPIC_API_KEY가 이미 설정돼 있으면 제거 — Claude Code가 이를 우선 감지해 프롬프트 띄움
            unset ANTHROPIC_API_KEY 2>/dev/null || true
            unset CLAUDE_CODE_USE_BEDROCK 2>/dev/null || true
            # Claude Code 2.x는 ANTHROPIC_AUTH_TOKEN + AWS IRSA env(AWS_ROLE_ARN,
            # AWS_WEB_IDENTITY_TOKEN_FILE) 조합을 감지하면 Bedrock API Keys 모드로 자동 전환해
            # JWT를 Bearer로 bedrock-runtime에 직접 전달 — Bedrock이 prefix 검증 실패로
            # 403 "Invalid API Key format: Must start with pre-defined prefix" 응답.
            # auth-gateway /v1 경로만 쓰도록 IRSA env를 모두 제거.
            unset AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE 2>/dev/null || true
            unset AWS_STS_REGIONAL_ENDPOINTS AWS_DEFAULT_REGION AWS_REGION 2>/dev/null || true
            # Anthropic-compatible 프로토콜이므로 model ID는 Anthropic-style로 교체.
            # Claude Code가 자체 카탈로그로 client-side validation하므로 Bedrock profile ID
            # (`global.anthropic.*`)을 쓰면 "may not exist" 오류 발생.
            # auth-gateway bedrock_proxy.py의 MODEL_MAP이 이를 다시 Bedrock ID로 resolve.
            export ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-4-6"
            export ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-haiku-4-5"
            # Claude Code 2.x는 ANTHROPIC_BASE_URL 값 뒤에 `/v1/messages`를 자체적으로 붙임.
            # 주입된 값이 이미 `/v1`로 끝나면 최종 URL이 `/v1/v1/messages` → 404.
            # (auth-gateway k8s_service.py의 주입값을 수정하기 전까지의 컨테이너-측 보정)
            export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL%/v1}"
            echo "  Proxy:    ${ANTHROPIC_BASE_URL} (Bedrock AG T20, JWT issued)"
        else
            echo "  Proxy:    JWT 파싱 실패 — Bedrock 직접 경로 유지"
        fi
    else
        echo "  Proxy:    pod-token-exchange 실패 — Bedrock 직접 경로 유지"
    fi

    unset _JWT_RESPONSE _ACCESS_TOKEN _REFRESH_TOKEN _AG_POD_NAME
fi

# ---------------------------------------------------------------------------
# T20 Phase 1c: Background token refresh daemon
# 목적: JWT access_token TTL(15분) 만료 전 자동 refresh. 장시간 CLI 세션 유지.
# 동작: 600초(10분) 주기 — 15분 TTL 대비 5분 여유.
#   1. ~/.claude-refresh-token 읽음
#   2. POST /auth/refresh with body={"refresh_token": "..."}
#   3. 응답의 새 access_token + refresh_token을 파일에 기록
#   4. 연속 3회 실패 시 경고 로그 (daemon 중단 안 함)
# 파일 기반 교환: bash export는 자식 프로세스로 전파 안 되므로 파일로 전달.
# Claude Code 프로세스는 기존 토큰 유지 — rotation 중에도 호출 영향 없음
# (auth-gateway는 블랙리스트된 jti만 차단, access_token은 TTL까지 유효).
# ---------------------------------------------------------------------------
if [ -n "${AUTH_GATEWAY_URL:-}" ] && [ -f /home/node/.claude-refresh-token ]; then
    (
        _FAIL_COUNT=0
        while true; do
            sleep 600
            _REFRESH=$(cat /home/node/.claude-refresh-token 2>/dev/null)
            if [ -z "${_REFRESH}" ]; then
                continue
            fi
            _RESP=$(curl -sf -X POST \
                "${AUTH_GATEWAY_URL}/auth/refresh" \
                -H "Content-Type: application/json" \
                -d "{\"refresh_token\":\"${_REFRESH}\"}" \
                --max-time 10 2>/dev/null || echo "")
            if [ -n "${_RESP}" ]; then
                _NEW_ACCESS=$(echo "${_RESP}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")
                _NEW_REFRESH=$(echo "${_RESP}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('refresh_token',''))" 2>/dev/null || echo "")
                if [ -n "${_NEW_ACCESS}" ]; then
                    echo "${_NEW_ACCESS}" > /home/node/.claude-token
                    chmod 600 /home/node/.claude-token
                    if [ -n "${_NEW_REFRESH}" ]; then
                        echo "${_NEW_REFRESH}" > /home/node/.claude-refresh-token
                        chmod 600 /home/node/.claude-refresh-token
                    fi
                    _FAIL_COUNT=0
                    # stdout 로그는 /dev/pts 가 아닌 ttyd 로그로 남김
                    echo "[$(date '+%H:%M:%S')] Token refreshed" >> /tmp/token-refresh.log
                else
                    _FAIL_COUNT=$((_FAIL_COUNT + 1))
                fi
            else
                _FAIL_COUNT=$((_FAIL_COUNT + 1))
            fi
            if [ "${_FAIL_COUNT}" -ge 3 ]; then
                echo "[$(date '+%H:%M:%S')] WARNING: token refresh failed 3 times" >> /tmp/token-refresh.log
                _FAIL_COUNT=0
            fi
        done
    ) &
    echo "  Token refresh daemon started (600s interval)"
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

# Git HTTP 프록시 — HTTPS_PROXY가 주입되어 있으면 git config에도 복제.
if [ -n "${HTTPS_PROXY:-}" ]; then
    git config --global http.proxy "${HTTPS_PROXY}"
    git config --global https.proxy "${HTTPS_PROXY}"
    echo "  Git proxy 설정 완료 (egress authenticated)"
fi

# --- Gitea git setup ---
# GITEA_USER/GITEA_TOKEN가 주입된 경우, gitconfig.template을 렌더링하여
# 내부 Gitea 서버용 자격증명 + 전역 hooks 경로(core.hooksPath)를 설정한다.
if [[ -n "${GITEA_USER:-}" && -n "${GITEA_TOKEN:-}" ]]; then
  # USER_DISPLAY_NAME 미주입 시 SSO ID로 폴백 (기존 동작 유지)
  export USER_DISPLAY_NAME="${USER_DISPLAY_NAME:-$GITEA_USER}"
  # envsubst에 변수 화이트리스트를 명시 — 템플릿의 의도치 않은 $var 치환 방지
  envsubst '${GITEA_USER} ${USER_DISPLAY_NAME}' < /home/node/.gitconfig.template > /home/node/.gitconfig
  chmod 600 /home/node/.gitconfig

  # 크리덴셜은 내부 클러스터 DNS로 저장 — 외부 URL은 Pod 안에서 resolv 불가
  echo "http://${GITEA_USER}:${GITEA_TOKEN}@gitea-http.gitea.svc.cluster.local:3000" > /home/node/.git-credentials
  chmod 600 /home/node/.git-credentials

  # template 렌더 후 프록시 재적용 (template 덮어쓰기로 이전 git config --global 소실)
  if [ -n "${HTTPS_PROXY:-}" ]; then
      git config --global http.proxy "${HTTPS_PROXY}"
      git config --global https.proxy "${HTTPS_PROXY}"
  fi
  # Gitea 내부 URL은 프록시 우회 — 클러스터 내부 직접 연결
  git config --global http."http://gitea-http.gitea.svc.cluster.local:3000/".proxy ""
  # GitHub SSH → HTTPS 리다이렉트 (Pod에 ssh binary 없음)
  git config --global url."https://github.com/".insteadOf "git@github.com:"
  git config --global --add url."https://github.com/".insteadOf "ssh://git@github.com/"
  # GitHub + raw.githubusercontent.com 프록시 우회
  git config --global http."https://github.com".proxy ""
  git config --global http."https://raw.githubusercontent.com".proxy ""

  echo "[entrypoint] Gitea git configured for user: $GITEA_USER"
else
  echo "[entrypoint] WARNING: GITEA_USER/GITEA_TOKEN not set — git operations will fail"
fi
# --- end ---

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
    # 모든 cp에 `|| true` — 과거 Pod에서 Claude Code를 실행하지 않은 사용자는
    # history.jsonl 등 일부 파일이 백업에 없을 수 있음. set -e 환경에서
    # 개별 cp 실패가 entrypoint 전체를 exit 1로 종료시키는 것을 방지.
    cp -r /home/node/workspace/.claude-backup/projects/ /home/node/.claude/projects/ 2>/dev/null || true
    cp /home/node/workspace/.claude-backup/history.jsonl /home/node/.claude/history.jsonl 2>/dev/null || true
    cp -r /home/node/workspace/.claude-backup/sessions/ /home/node/.claude/sessions/ 2>/dev/null || true
    echo "  이전 대화 복원 완료"
fi
# 사용자 프로젝트 CLAUDE.md(~/CLAUDE.md) 복원 — 자동 생성되는 ~/.claude/CLAUDE.md 와 다름
if [ -f /home/node/workspace/.claude-backup/user-CLAUDE.md ]; then
    cp /home/node/workspace/.claude-backup/user-CLAUDE.md /home/node/CLAUDE.md 2>/dev/null
    echo "  사용자 CLAUDE.md(~/CLAUDE.md) 복원 완료"
fi
# 커스텀 slash commands 복원 (~/.claude/commands/)
if [ -d /home/node/workspace/.claude-backup/commands ]; then
    mkdir -p /home/node/.claude/commands
    cp -r /home/node/workspace/.claude-backup/commands/. /home/node/.claude/commands/ 2>/dev/null
    echo "  커스텀 slash commands 복원 완료"
fi
# 커스텀 skills 복원 (~/.claude/skills/)
if [ -d /home/node/workspace/.claude-backup/skills ]; then
    mkdir -p /home/node/.claude/skills
    cp -r /home/node/workspace/.claude-backup/skills/. /home/node/.claude/skills/ 2>/dev/null
    echo "  커스텀 skills 복원 완료"
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
# 사용자 프로젝트 CLAUDE.md(~/CLAUDE.md) 백업 — Pod 재기동 시 유실 방지
if [ -f /home/node/CLAUDE.md ]; then
    cp /home/node/CLAUDE.md /home/node/workspace/.claude-backup/user-CLAUDE.md 2>/dev/null
fi
# 커스텀 slash commands 백업 (~/.claude/commands/)
if [ -d /home/node/.claude/commands ]; then
    rm -rf /home/node/workspace/.claude-backup/commands 2>/dev/null
    cp -r /home/node/.claude/commands /home/node/workspace/.claude-backup/commands 2>/dev/null
fi
# 커스텀 skills 백업 (~/.claude/skills/)
if [ -d /home/node/.claude/skills ]; then
    rm -rf /home/node/workspace/.claude-backup/skills 2>/dev/null
    cp -r /home/node/.claude/skills /home/node/workspace/.claude-backup/skills 2>/dev/null
fi
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
# 사용자 프로젝트 CLAUDE.md(~/CLAUDE.md) 복원
if [ -f /home/node/workspace/.claude-backup/user-CLAUDE.md ]; then
    cp /home/node/workspace/.claude-backup/user-CLAUDE.md /home/node/CLAUDE.md 2>/dev/null
    echo "사용자 CLAUDE.md(~/CLAUDE.md) 복원 완료."
fi
# 커스텀 slash commands 복원
if [ -d /home/node/workspace/.claude-backup/commands ]; then
    mkdir -p /home/node/.claude/commands
    cp -r /home/node/workspace/.claude-backup/commands/. /home/node/.claude/commands/ 2>/dev/null
    echo "커스텀 slash commands 복원 완료."
fi
# 커스텀 skills 복원
if [ -d /home/node/workspace/.claude-backup/skills ]; then
    mkdir -p /home/node/.claude/skills
    cp -r /home/node/workspace/.claude-backup/skills/. /home/node/.claude/skills/ 2>/dev/null
    echo "커스텀 skills 복원 완료."
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
# 4c) 유휴 감지 heartbeat (5분마다) — 브라우저 접속 + 탭 표시 중일 때만 전송
#     조건 2가지 AND로 판정:
#       1. ttyd 포트(7681=0x1E01)에 ESTABLISHED 연결 있음 (브라우저 열림)
#       2. /tmp/.tab-hidden 플래그 파일 없음 (탭이 화면에 보임)
#     둘 중 하나라도 깨지면 heartbeat 중단 → 30분 후 idle cleanup이 Pod 종료.
#     플래그는 fileserver.py /api/visibility 엔드포인트가 브라우저의
#     document.visibilitychange 이벤트 신호를 받아 생성/삭제한다.
# ---------------------------------------------------------------------------
if [ -n "${AUTH_GATEWAY_URL:-}" ]; then
    POD_NAME="claude-terminal-$(echo ${USER_ID} | tr '[:upper:]' '[:lower:]')"
    (while true; do
        sleep 300
        # 포트 7681(0x1E01)에 ESTABLISHED(01) 연결 확인
        ACTIVE=$(awk '$2 ~ /:1E01$/ && $4 == "01"' /proc/net/tcp 2>/dev/null | wc -l)
        # 탭 가림 플래그 확인 — 있으면 유휴 간주
        if [ "$ACTIVE" -gt 0 ] && [ ! -f /tmp/.tab-hidden ]; then
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
# 5b) Claude Code 플러그인 설치 (로컬 marketplace 기반, 1회만)
# 이미지에 slim marketplace가 pre-bake 되어 있고 known_marketplaces.json이
# lastUpdated=2099로 자동 refresh를 차단하므로, 여기서는 3개 플러그인을
# marketplace의 로컬 소스에서 install만 하면 된다. GitHub/git clone 의존 없음.
# ---------------------------------------------------------------------------
if [ ! -f /home/node/workspace/.plugins-installed ]; then
    echo "  플러그인 설치 중..."
    for p in superpowers feature-dev frontend-design; do
        claude plugin install "${p}@claude-plugins-official" >/dev/null 2>&1 || true
    done
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

# T20 Phase 1c: 최신 access_token을 파일에서 로드 (refresh daemon이 갱신)
# ttyd가 재연결 또는 사용자가 claude를 재시작할 때마다 실행되므로
# 최신 토큰이 ANTHROPIC_AUTH_TOKEN에 주입됨.
if [ -f /home/node/.claude-token ]; then
    _FRESH_TOKEN=$(cat /home/node/.claude-token 2>/dev/null)
    if [ -n "${_FRESH_TOKEN}" ]; then
        export ANTHROPIC_AUTH_TOKEN="${_FRESH_TOKEN}"
    fi
    unset _FRESH_TOKEN
fi

# 모드 선택 — ~/.claude-mode 에 저장된 값 기반으로 분기
# 1 = 답변만 받기 (기본) — ask-loop REPL
# 2 = 진행 과정 자세히 보기 — 기존 claude TUI
MODE_FILE="/home/node/.claude-mode"
MODE=$(cat "${MODE_FILE}" 2>/dev/null | tr -d '[:space:]')

if [ -z "${MODE}" ]; then
    # 최초 접속 — 선택 화면
    echo ""
    echo "  ╔══════════════════════════════════════════════════════╗"
    echo "  ║  Claude Code — 시작 모드 선택                        ║"
    echo "  ╠══════════════════════════════════════════════════════╣"
    echo "  ║                                                      ║"
    echo "  ║  [1] 답변만 받기  (추천)                             ║"
    echo "  ║      • 최종 답변만 표시                              ║"
    echo "  ║      • 진행 과정(사고/도구 호출) 숨김                ║"
    echo "  ║                                                      ║"
    echo "  ║  [2] 진행 과정 자세히 보기                           ║"
    echo "  ║      • 코드 작업, 도구 호출, 사고 과정 전부 표시     ║"
    echo "  ║      • 개발자·전문가 권장                            ║"
    echo "  ║                                                      ║"
    echo "  ║  (언제든 'switch-mode' 명령 또는 상단 버튼으로       ║"
    echo "  ║   전환 가능)                                         ║"
    echo "  ╚══════════════════════════════════════════════════════╝"
    echo ""
    read -r -p "  선택 [1/2, 기본 1]: " MODE
    [ "${MODE}" != "2" ] && MODE="1"
    echo "${MODE}" > "${MODE_FILE}"
    echo ""
fi

if [ "${MODE}" = "1" ]; then
    exec /usr/local/bin/ask-loop
fi

echo ""
echo "  Claude Code를 시작합니다... (진행 과정 자세히 보기 모드)"
echo ""

# Run Claude Code — 자세히 보기 모드
# --continue 옵션 제거: 이전 대화 자동 이어받기 없음 (매 세션 새로 시작)
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
    --max-clients 3 \
    --client-option reconnect=3 \
    --client-option titleFixed="Claude Code Terminal" \
    --client-option rendererType=dom \
    --client-option disableLeaveAlert=true \
    --client-option allowProposedApi=true \
    /home/node/.local/bin/claude-wrapper
