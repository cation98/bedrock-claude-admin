# JWT Pod 세션 TTL 연장 설계 (Issue #27)

**작성일**: 2026-04-14
**이슈**: [#27 auth: JWT 15분 만료 UX 개선](https://github.com/cation98/bedrock-ai-agent/issues/27)
**브랜치**: `phase1-backlog/#27-jwt-ttl`

## 1. 문제

사용자 Pod 터미널 세션 중 `pod-token-exchange`로 받은 JWT가 15분 후 만료되어 `401` 발생. Claude Code 프로세스가 env var `ANTHROPIC_AUTH_TOKEN`을 시작 시점에 고정 캡처하므로, shell-level refresh daemon이 파일을 갱신해도 실행 중인 프로세스에는 반영 안 됨 → 15분 경과 시 사용자는 Pod 재기동 필요.

## 2. 제약

- `ANTHROPIC_AUTH_TOKEN` env var는 claude 프로세스 생존 기간 동안 고정
- 기존 SSO/portal 웹 세션은 15분 TTL 유지 필요 (보안팀 기존 승인 범위)
- `/auth/refresh` 엔드포인트는 **SSO/portal + Pod refresh daemon** 양쪽에서 호출됨

## 3. 결정: A.2-extended (session_type 클레임 기반 분기)

JWT payload에 `session_type` 클레임을 심어 토큰 간에 상속시키고, 호출 컨텍스트에 따라 TTL을 선택적으로 연장한다.

### 3.1 토큰 발급 매트릭스

| 발급 경로 | access TTL | refresh TTL | session_type |
|----------|-----------|-------------|--------------|
| SSO 로그인 (기존) | 15m | 12h | 미설정 (기본) |
| Portal 활동 갱신 (기존) | 15m | 12h | 미설정 |
| `/auth/pod-token-exchange` | **8h** | **12h (변경없음)** | `"pod"` |
| `/auth/refresh` with session_type="pod" | **8h** | **12h** | `"pod"` (상속) |
| `/auth/refresh` without session_type | 15m | 12h | 미설정 |

### 3.2 변경 범위

- `auth-gateway/app/core/jwt_rs256.py` — `create_access_token`/`create_refresh_token` 시그니처에 `expires_delta: Optional[timedelta]=None` + `extra_claims: Optional[dict]=None` 추가. 기본값 `None` → 기존 동작 유지.
- `auth-gateway/app/routers/jwt_auth.py:205` (pod-token-exchange) — `extra_claims={"session_type": "pod"}`, access에 `expires_delta=timedelta(hours=8)` 전달.
- `auth-gateway/app/routers/jwt_auth.py:300` (`/auth/refresh`) — 검증된 refresh 페이로드의 `session_type` 읽어서 `"pod"`면 `expires_delta=timedelta(hours=8)` + `extra_claims={"session_type": "pod"}` 둘 다 새 access_token에 전달.

### 3.3 Refresh token TTL

현재 12h 유지. Pod 세션이 12h를 초과하면 refresh daemon이 refresh 자체를 못하고 사용자는 Pod 재기동 필요 — 이는 업무시간 경계와 일치하므로 허용.

## 4. 대안 (기각 사유)

- **A.1 전역 TTL 상향** — SSO/portal까지 영향, blast radius 確大
- **A.3 전용 함수 신설 (`create_pod_access_token`)** — create_access_token과 코드 중복
- **A.4 config 필드 추가 + 컨텍스트 분기** — 함수 내부에서 "pod 여부" 판단 어색
- **B 사이드카 refresh proxy** — 신규 인프라 배포, 리소스 증가, Phase 2로 연기
- **C 401 감지 재시작** — 대화 유실, UX 저하

## 5. 보안 고려

- **탈취 시 blast radius**: pod 토큰 탈취 시 최대 8h 유효. 대안: 2h로 조정 가능 (현재 업무 반나절 기준 8h 선택).
- **Cascade revoke 정책 변경 없음**: 기존 `revoke_all_refresh_for_user` 로직 그대로 유효. replay 감지 시 전체 세션 revoke.
- **session_type 위조 방지**: RS256 서명으로 페이로드 무결성 보장. 클라이언트는 클레임 변경 불가.
- **로깅**: pod-token-exchange 및 pod-session refresh 시 access TTL=8h를 명시 로그에 기록.

## 6. 테스트 전략

### 단위 (auth-gateway/tests/ 내)

- `test_create_access_token_default_ttl` — `expires_delta` 미지정 시 `settings.jwt_rs256_access_expire_minutes` 적용
- `test_create_access_token_custom_ttl` — `expires_delta=timedelta(hours=8)` 적용 확인 (exp - iat 차이 검증)
- `test_create_access_token_extra_claims` — `extra_claims` 페이로드에 포함됨
- `test_create_refresh_token_custom_ttl` — 동일 패턴
- (회귀) 기존 create_access_token 사용처가 고장나지 않음 확인

### 통합 (실 엔드포인트)

- `test_pod_token_exchange_issues_long_ttl` — pod-token-exchange 응답 access_token의 exp가 대략 8h 후
- `test_pod_refresh_preserves_long_ttl` — pod session_type 가진 refresh_token으로 /auth/refresh 호출 시 새 access_token도 8h
- `test_sso_refresh_unchanged_15m` — session_type 없는 refresh token으로 /auth/refresh 호출 시 새 access_token은 15m
- `test_session_type_cascade_on_pod_refresh` — pod /auth/refresh 응답 access에도 session_type="pod" 유지

### 수동 e2e

- 기존 터미널 Pod 스폰 → Claude Code 시작 → 20분 후(기존 15m 경과) 호출 시 정상 응답 확인
- refresh daemon 10분 주기 로그에서 새 토큰 TTL이 8h 임을 확인

## 7. 롤백

- `git revert` 로 코드 변경 되돌림. config/DB 변경 없으므로 런타임 데이터 손상 없음.
- 이미 발급된 8h 토큰은 revoke_all_refresh_for_user 또는 blacklist로 조기 종료 가능.

## 8. Deferred (이슈 설계 외)

- 사이드카 refresh proxy (옵션 B) — Phase 2 후보
- ANTHROPIC_AUTH_TOKEN을 env 대신 파일 기반으로 claude CLI에서 읽도록 upstream 수정 — 외부 기여 범위

## 9. 성공 판정 기준

- [ ] `jwt_rs256.py` 단위 테스트 추가 전부 통과
- [ ] `/auth/pod-token-exchange` 응답 access_token의 exp ≈ now + 8h (±1분)
- [ ] `/auth/refresh` with session_type="pod" 응답 access_token의 exp ≈ now + 8h
- [ ] `/auth/refresh` without session_type 응답 access_token의 exp ≈ now + 15m (회귀 방지)
- [ ] `/auth/login` SSO 응답 access_token exp ≈ now + 15m (회귀 방지)
- [ ] 전체 auth-gateway 기존 테스트 스위트 통과
