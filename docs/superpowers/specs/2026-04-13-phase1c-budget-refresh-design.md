# Phase 1c: CP-20 Budget Gate + T20 Token Refresh Daemon

**Date:** 2026-04-13
**Scope:** 2 items (reduced process, <50 line spec per user directive)

---

## Goal

- **CP-20 Budget Gate 실체화**: `/v1/messages` (bedrock_proxy)에 quota 검사 추가. 초과 시 429 반환.
- **T20 Token Refresh Daemon**: Console Pod에서 JWT access_token을 15분 TTL 만료 전 자동 refresh.

## Problem

1. **현재 gap**: `_check_user_quota()`는 Pod 세션 생성 시점(sessions.py)에만 호출됨. 이미 Pod이 떠 있는 사용자는 Bedrock을 quota 초과 후에도 무제한 호출 가능.
2. **15분 TTL**: `ANTHROPIC_AUTH_TOKEN`은 Pod 부팅 시 1회만 발급. 15분 후 만료 → Claude Code 401 에러 → 사용자가 세션 재시작 필요.

## Design

### CP-20 Budget Gate (auth-gateway/app/routers/bedrock_proxy.py)

- `messages()` 엔드포인트 진입 직후 `_check_user_quota(db, username)` 호출.
- `is_exceeded and not is_unlimited` → `HTTPException(429, detail={error, limit, used, cycle})`.
- quota assignment가 없는 사용자 (신규) → 통과 (세션 생성 시 정책 배정이 정식 경로).
- DB 세션: `SessionLocal()` (request-scoped), finally close.

### T20 Token Refresh Daemon (container-image/entrypoint.sh)

- `/auth/refresh` 엔드포인트 이미 존재 (jwt_auth.py).
- `ANTHROPIC_AUTH_TOKEN` 발급 후 background bash loop 추가:
  - 600초(10분) 주기 — 15분 TTL 대비 5분 여유.
  - `POST ${AUTH_GATEWAY_URL}/auth/refresh` with current token (Authorization: Bearer).
  - 응답 파싱 → `export ANTHROPIC_AUTH_TOKEN` 갱신.
  - 실패 3회 연속 → warning 로그 (daemon은 계속 실행).
- 이슈: bash background process의 export가 claude 프로세스에 전파 안 됨 → **파일 기반 교환**.
  - Refresh daemon이 `~/.claude-token` 파일에 새 토큰 기록.
  - claude-wrapper가 새 쉘 생성 시 `ANTHROPIC_AUTH_TOKEN=$(cat ~/.claude-token)` 로드.
  - 대안: 기존 Claude Code 프로세스는 token rotation 중 영향 없도록 `/auth/refresh`로 기존 토큰 유효성 유지.

### /auth/refresh 적용 검증

- `jwt_auth.py:101`에 refresh endpoint 존재. refresh_token 쿠키 또는 body로 받음.
- Pod Token Exchange 응답에 `refresh_token` 포함되는지 확인 → 포함됨 (jwt_auth.py:203-215).
- Console Pod에 `refresh_token`을 환경변수나 파일로 전달 필요 → entrypoint에 추가.

## Acceptance Criteria

- **Unit**: `tests/unit/test_bedrock_proxy_budget.py` — quota 초과 시 429, 미배정 시 200, 초과 직전 200.
- **Integration**: 수동 확인 — quota=$0.01 배정 후 `/v1/messages` 호출 시 429.
- **Refresh**: entrypoint 로그에 "Token refreshed" 주기 출력 + 15분 이상 세션 유지 후 claude 호출 200.

## Out of Scope

- HPA scaling, Redis-based rate limit, PR/회계 감사 alert.
- 추후 Phase (1d?) 검토.

## Files

- Modify: `auth-gateway/app/routers/bedrock_proxy.py` (+budget_gate dependency).
- Modify: `container-image/entrypoint.sh` (+refresh daemon).
- Modify: `auth-gateway/app/routers/jwt_auth.py` (pod-token-exchange 응답에 refresh_token 포함 확인).
- Create: `tests/unit/test_bedrock_proxy_budget.py`.
