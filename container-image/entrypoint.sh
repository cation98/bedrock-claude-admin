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

# CLAUDE.md에 사용자 프로필 섹션 추가
cat >> /home/node/CLAUDE.md << USERPROFILE

## 현재 사용자 정보

- **사번**: ${USER_ID}
- **이름**: ${USER_DISPLAY_NAME}
USERPROFILE

# CLAUDE.md에 데이터 소스 정보 추가
cat >> /home/node/CLAUDE.md << 'DATASOURCES'

## 사용 가능한 데이터 소스

### 1. 안전관리 DB (Safety)
- **접속**: `psql $DATABASE_URL` 또는 `psql -h $SAFETY_DB_HOST -U claude_readonly -d safety`
- **용도**: 안전관리시스템 데이터 조회
- **권한**: ReadOnly

### 2. TANGO 알람 DB (네트워크 고장)
- **접속**: `psql $TANGO_DATABASE_URL` 또는 `psql -h $TANGO_DB_HOST -U claude_readonly -d postgres`
- **용도**: SK텔레콤 네트워크 실시간 고장/알람 데이터
- **주요 테이블**:
  - `alarm_data` — 현재 활성 고장 (7일 보존)
  - `alarm_events` — 전체 이벤트 로그 (30일 보존, 분석용)
  - `alarm_history` — 복구된 고장 이력
  - `facility_info` — 장비 마스터 데이터 (JSONB)
  - `alarm_hourly_summary` — 시간대별 집계
  - `alarm_statistics` — 운용팀별 현황 뷰
- **권한**: ReadOnly
- **알람 상태값**: O(발생), U(미확인), L(잠금) = 활성 | C(복구), F(사용자복구), A(인지), D(삭제) = 해제
- **주요 컬럼**: EQP_NM(장비명), FALT_OCCR_LOC_CTT(고장위치), OP_TEAM_ORG_NM(운용팀), EVT_TIME(발생시각), ALM_STAT_VAL(상태)

### 3. S3 아카이브 (AWS Athena 쿼리)
- **버킷**: `s3://tango-alarm-logs/raw/` (1년 보존)
- **Athena DB**: `tango_logs`, 테이블: `alarm_events_archive`
- **쿼리 방법**: `aws athena start-query-execution` 또는 Python boto3
- **용도**: 30일 이상 과거 알람 데이터 분석

### 예시 쿼리

```sql
-- 현재 활성 고장 현황 (팀별)
SELECT OP_TEAM_ORG_NM, COUNT(*) FROM alarm_data GROUP BY OP_TEAM_ORG_NM ORDER BY COUNT(*) DESC;

-- 최근 24시간 고장 추이
SELECT date_trunc('hour', received_at) AS hour, COUNT(*) FROM alarm_events
WHERE received_at > NOW() - INTERVAL '24 hours' GROUP BY hour ORDER BY hour;

-- 특정 장비 고장 이력
SELECT EVT_TIME, ALM_STAT_VAL, ALM_DESC FROM alarm_events
WHERE EQP_NM LIKE '%장비명%' ORDER BY received_at DESC LIMIT 20;
```
DATASOURCES

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
    bash -l
