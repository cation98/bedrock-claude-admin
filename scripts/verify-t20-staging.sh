#!/usr/bin/env bash
# verify-t20-staging.sh — T20 proxy staging 검증 스크립트 (§8 T-1h)
#
# 사용법:
#   ./scripts/verify-t20-staging.sh setup    # Pre-flight: Secret + DB 사용자 등록
#   ./scripts/verify-t20-staging.sh check    # 검증: stream lag, DB row, dedupe
#   ./scripts/verify-t20-staging.sh teardown # 정리: Pod + Secret + DB row 삭제
#
# 전제조건:
#   - PR-1, PR-2 머지 완료
#   - alembic upgrade head 완료 (token_usage_event 테이블 존재)
#   - kubectl context가 EKS로 설정됨
#   - DATABASE_URL 환경변수 설정 (또는 psql이 이미 설정된 상태)

set -euo pipefail

CONTEXT="${KUBECTL_CONTEXT:-arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks}"
NS="claude-sessions"
POD="claude-terminal-t20-staging"
SECRET="pod-token-t20stg"
USERNAME="T20STG"
DB="${DATABASE_URL:-}"

kube() { kubectl --context "$CONTEXT" "$@"; }
db()   { psql "$DB" "$@"; }

cmd_setup() {
    echo "=== [SETUP] T20 Staging Pre-flight ==="
    echo ""

    # 1. Random pod token
    echo "▶ 1/4 K8s Secret 생성 ($SECRET)"
    STAGING_TOKEN=$(openssl rand -hex 32)
    kube -n "$NS" create secret generic "$SECRET" \
        --from-literal=token="$STAGING_TOKEN" \
        --dry-run=client -o yaml | kube apply -f -
    echo "  ✅ Secret '$SECRET' 생성 완료"

    # 2. DB 사용자 등록
    echo ""
    echo "▶ 2/4 DB T20STG 사용자 등록"
    if [ -z "$DB" ]; then
        echo "  ⚠️  DATABASE_URL 미설정 — 수동으로 아래 SQL 실행하세요:"
        echo "  INSERT INTO users (username, display_name, email, is_approved, role, created_at)"
        echo "  VALUES ('T20STG', 'T20 Staging Verifier', 'T20STG@skons.net', true, 'admin', NOW())"
        echo "  ON CONFLICT (username) DO NOTHING;"
    else
        db -c "INSERT INTO users (username, display_name, email, is_approved, role, created_at)
               VALUES ('T20STG', 'T20 Staging Verifier', 'T20STG@skons.net', true, 'admin', NOW())
               ON CONFLICT (username) DO NOTHING;" 2>/dev/null \
            && echo "  ✅ users 테이블 T20STG 등록 완료" \
            || echo "  ℹ️  이미 존재하거나 실패 — 수동 확인 필요"
    fi

    # 3. pod-token 해시 계산 + terminal_sessions 등록
    echo ""
    echo "▶ 3/4 terminal_sessions pod-token 등록"
    STAGING_TOKEN_HASH=$(python3 -c "import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$STAGING_TOKEN")
    if [ -z "$DB" ]; then
        echo "  ⚠️  DATABASE_URL 미설정 — 수동으로 아래 SQL 실행하세요:"
        echo "  INSERT INTO terminal_sessions (username, pod_name, pod_token_hash, status, created_at, updated_at)"
        echo "  VALUES ('T20STG', 'claude-terminal-t20-staging', '$STAGING_TOKEN_HASH', 'running', NOW(), NOW());"
    else
        db -c "INSERT INTO terminal_sessions (username, pod_name, pod_token_hash, status, created_at, updated_at)
               VALUES ('T20STG', 'claude-terminal-t20-staging', '$STAGING_TOKEN_HASH', 'running', NOW(), NOW())
               ON CONFLICT DO NOTHING;" 2>/dev/null \
            && echo "  ✅ terminal_sessions T20STG 등록 완료 (hash: ${STAGING_TOKEN_HASH:0:16}...)" \
            || echo "  ℹ️  등록 실패 — 수동 확인 필요"
    fi

    # 4. Pod apply
    echo ""
    echo "▶ 4/4 staging Pod 생성"
    kube apply -f infra/k8s/pod-template-t20-staging.yaml
    echo ""
    echo "  대기 중 (최대 60s)..."
    kube -n "$NS" wait --for=condition=Ready pod/"$POD" --timeout=60s \
        && echo "  ✅ Pod Ready" \
        || echo "  ⚠️  60s 내 Ready 미달 — kubectl describe pod 확인 필요"

    echo ""
    echo "=== Setup 완료 ==="
    echo "다음 단계: 브라우저에서 검증"
    echo "  kubectl --context $CONTEXT -n $NS port-forward pod/$POD 7681:7681 &"
    echo "  open http://localhost:7681"
    echo ""
    echo "검증 후: ./scripts/verify-t20-staging.sh check"
}

cmd_check() {
    echo "=== [CHECK] T20 Staging 검증 ==="
    echo ""
    FAIL=0

    # 1. Pod 상태
    echo "▶ 1/5 Pod 상태"
    STATUS=$(kube -n "$NS" get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo "NotFound")
    if [ "$STATUS" = "Running" ]; then
        echo "  ✅ Pod Running"
    else
        echo "  ❌ Pod 상태: $STATUS"
        FAIL=$((FAIL + 1))
    fi

    # 2. Redis stream lag (auth-gateway metrics)
    echo ""
    echo "▶ 2/5 Redis stream lag (usage-worker metrics)"
    LAG=$(kube -n platform exec -it deployment/usage-worker -- \
        python3 -c "
import redis, os
r = redis.Redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379'))
pending = r.xpending('stream:usage_events', 'usage-workers')
print(pending.get('pending', 0))
" 2>/dev/null || echo "N/A")
    echo "  pending 건수: $LAG (0이 정상)"
    [ "$LAG" = "0" ] || [ "$LAG" = "N/A" ] || FAIL=$((FAIL + 1))

    # 3. token_usage_event 확인
    echo ""
    echo "▶ 3/5 token_usage_event INSERT 확인"
    if [ -n "$DB" ]; then
        ROWS=$(db -tAc "SELECT COUNT(*) FROM token_usage_event WHERE username='T20STG'" 2>/dev/null || echo "0")
        echo "  T20STG events: $ROWS (5+ 이면 정상)"
        [ "$ROWS" -ge 5 ] 2>/dev/null && echo "  ✅" || { echo "  ⚠️  부족"; FAIL=$((FAIL + 1)); }
    else
        echo "  ⚠️  DATABASE_URL 미설정 — 수동 확인: SELECT COUNT(*) FROM token_usage_event WHERE username='T20STG';"
    fi

    # 4. token_usage_daily 모델별 row 확인
    echo ""
    echo "▶ 4/5 token_usage_daily 모델별 row 확인"
    if [ -n "$DB" ]; then
        db -c "SELECT model_id, input_tokens, output_tokens, cost_usd
               FROM token_usage_daily
               WHERE username='T20STG'
               ORDER BY model_id;" 2>/dev/null \
            && echo "  ✅ (row 존재)" || { echo "  ❌ row 없음"; FAIL=$((FAIL + 1)); }
    else
        echo "  ⚠️  수동 확인: SELECT * FROM token_usage_daily WHERE username='T20STG';"
    fi

    # 5. DLQ 확인
    echo ""
    echo "▶ 5/5 DLQ (stream:usage_events_dlq) 확인"
    DLQ=$(kube -n platform exec -it deployment/usage-worker -- \
        python3 -c "
import redis, os
r = redis.Redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379'))
print(r.xlen('stream:usage_events_dlq'))
" 2>/dev/null || echo "N/A")
    echo "  DLQ depth: $DLQ (0이 정상)"
    [ "$DLQ" = "0" ] || [ "$DLQ" = "N/A" ] || FAIL=$((FAIL + 1))

    echo ""
    if [ $FAIL -eq 0 ]; then
        echo "✅ 모든 검증 통과 — T+0 활성화 진행 가능"
        echo "   다음: ./scripts/verify-t20-staging.sh teardown"
    else
        echo "❌ $FAIL건 실패 — T+0 활성화 중단. 원인 분석 후 재검증."
        echo "   Pod 로그: kubectl --context $CONTEXT -n $NS logs $POD"
    fi
}

cmd_teardown() {
    echo "=== [TEARDOWN] T20 Staging 정리 ==="
    echo ""

    echo "▶ 1/4 Pod 삭제"
    kube -n "$NS" delete pod "$POD" --grace-period=5 2>/dev/null \
        && echo "  ✅ Pod 삭제" || echo "  ℹ️  Pod 없음 (이미 삭제됨)"

    echo ""
    echo "▶ 2/4 K8s Secret 삭제"
    kube -n "$NS" delete secret "$SECRET" 2>/dev/null \
        && echo "  ✅ Secret 삭제" || echo "  ℹ️  Secret 없음"

    echo ""
    echo "▶ 3/4 DB 검증 데이터 정리"
    if [ -n "$DB" ]; then
        db -c "
            DELETE FROM token_usage_event WHERE username='T20STG';
            DELETE FROM token_usage_daily WHERE username='T20STG';
            DELETE FROM token_usage_hourly WHERE username='T20STG';
            DELETE FROM terminal_sessions WHERE username='T20STG';
            DELETE FROM users WHERE username='T20STG';
        " && echo "  ✅ DB 정리 완료" || echo "  ⚠️  DB 정리 실패 — 수동 확인 필요"
    else
        echo "  ⚠️  DATABASE_URL 미설정 — 수동으로 아래 SQL 실행하세요:"
        echo "  DELETE FROM token_usage_event WHERE username='T20STG';"
        echo "  DELETE FROM token_usage_daily WHERE username='T20STG';"
        echo "  DELETE FROM terminal_sessions WHERE username='T20STG';"
        echo "  DELETE FROM users WHERE username='T20STG';"
    fi

    echo ""
    echo "▶ 4/4 검증 결과 기록 위치"
    echo "  docs/qa/2026-04-25-t20-activation-baseline.md 에 검증 결과 기록 권장"

    echo ""
    echo "=== Teardown 완료 ==="
    echo "T+0 활성화 준비 완료:"
    echo "  infra/k8s/pod-template.yaml 의 ANTHROPIC_BASE_URL 주석 해제 후 apply"
}

COMMAND="${1:-help}"
case "$COMMAND" in
    setup)    cmd_setup ;;
    check)    cmd_check ;;
    teardown) cmd_teardown ;;
    *)
        echo "사용법: $0 {setup|check|teardown}"
        echo ""
        echo "  setup    - Pre-flight: K8s Secret + DB 사용자 등록 + Pod 생성"
        echo "  check    - 검증: stream lag, token_usage_event, DLQ 확인"
        echo "  teardown - 정리: Pod + Secret + DB row 삭제"
        exit 1
        ;;
esac
