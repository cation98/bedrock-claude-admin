# Phase 1a — 보안 Hardening + ISMS-P Core 대응 설계

**Date**: 2026-04-12
**Author**: team-lead (Claude Opus 4.6 + cation98)
**Session**: phase0-merge-phase1-kickoff-2026-04-12 (mindbase 5887c1a2)
**Base**: main HEAD `d4d99eb` (Phase 0 merge)
**Parent session**: phase0-blockers-main-check-2026-04-12 (fdf2d2b5)

---

## 1. 목표 (Goal)

Phase 0 EKS 운영 상태에서 누적된 **보안 부채 6항목 + ISMS-P core 대응 1항목(Open WebUI data export)** 을 3일 내 해소하고 **내부 security audit PASS 판정**을 획득한다.

Phase 1a는 Phase 1 전체 로드맵에서 **첫 번째 sub-phase**이며 리스크 선제 순 분해 원칙에 따라 선행된다. Phase 1b(스케일/budget), Phase 1c(거버넌스)는 1a 완료 후 별도 스펙 사이클로 진입한다.

## 2. 배경 (Context)

- **Phase 0 완료 시점**: 2026-04-12 (36 commits, 72/72 core baseline, Drift 8/8, Security 13 items CLOSED, CP-18 TTFT 0.52s, Locust p95 37ms).
- **Phase 0에서 식별된 Phase 1 백로그 17+항목**: T17 합동 보고 + mindbase + Graphiti 저장됨.
- **대상 확장 target**: Phase 0(임원 15명 실습) → Phase 1(팀장 50명) → Phase 2(실무자 상시 10명).
- **Phase 1a 시점에 ISMS-P 외부 감사 예정 없음**. 내부 self-review 기준.

## 3. 범위 (Scope) — 7항목 확정

| # | 항목 | 카테고리 | 근거 |
|---|------|---------|------|
| 1 | **rediss:// transit encryption** | Infra 암호화 | Phase 0에서 data source 재활용 결정 시 Phase 1 TODO로 명시 |
| 2 | **docs_url=None, redoc_url=None, openapi_url=None** | API 표면 축소 | security iter#8 발견 (/docs 공개 노출) |
| 3 | **deterministic kid** (SHA256 기반) | JWKS rotation 안정화 | security iter#8 권고 |
| 4 | **WWW-Authenticate: Bearer** (RFC 표준) | 응답 헤더 표준 | security iter#8 권고 |
| 5 | **SameSite Strict 재검토** | 쿠키 정책 | security MEDIUM M-3 — Phase 0 Lax로 결정, Phase 1a 재평가 |
| 6 | **KMS key rotation 정책** | 자격증명 관리 | security + ISMS-P 통제 |
| 7 | **Open WebUI 데이터 export 스크립트** | ISMS-P 데이터 권리 | TODOS #21 |

### Out of scope (Phase 1b/1c로 이동)

- ElastiCache HA replication group → **1b**
- Budget gate + usage_emit 실체 구현 → **1b**
- T20 background token refresh daemon → **1b**
- `/auth/issue-jwt` endpoint → **1b**
- FileAuditAction Enum, Skills governance 스키마 → **1c**
- DEFAULT_USER_ROLE + model access_control → **1c**
- Locust cookie 인증 전환 → **1c**

## 4. 아키텍처 영향 (Affected components)

```
[Infra layer]
  ElastiCache (aws_elasticache_replication_group.main 또는 새 cluster)
    → transit_encryption_enabled = true
    → auth_token 생성 + K8s Secret `redis-auth-token` 신규
  KMS (aws_kms_key.s3_vault + bedrock_ag 있다면)
    → enable_key_rotation = true
    → deletion_window_in_days = 30 표준

[API layer]
  auth-gateway/app/main.py
    → FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
  auth-gateway/app/routers/auth.py
    → 전수 401 응답: WWW-Authenticate: Bearer realm="skons.net"
  auth-gateway/app/routers/jwt_auth.py
    → Cookie SameSite 재평가 (Strict 전환 여부 결정 문서 포함)
  auth-gateway/app/core/jwt_rs256.py
    → _key_id = SHA256(n_bytes || e_bytes)[:16]
  auth-gateway/app/core/config.py
    → REDIS_URL 타입: rediss:// 기본 + redis:// 하위 호환

[Data / ops]
  ops/export/chats.py      — 90일 이내 대화 JSONL/CSV
  ops/export/skills.py     — skills 목록 + approval_status
  ops/export/usage.py      — token_usage_daily Parquet
  ops/export/audit.py      — file_audit_logs JSONL (PII masking)
  ops/export/Dockerfile    — CI 컨테이너화
  ops/export/README.md     — 사용법 + IAM 권한 요구사항

[K8s manifests]
  infra/k8s/platform/redis-auth-token.yaml (신규 Secret)
  infra/k8s/platform/auth-gateway.yaml (REDIS_URL + redis-auth-token 참조)
  infra/k8s/openwebui/openwebui-pipelines.yaml (REDIS_URL)
  infra/k8s/platform/usage-worker.yaml (REDIS_URL)
```

## 5. 구현 전략 (Approach B — 3 batch 병렬)

### Batch 1 — Infra 보안 (devops + k8s)

**Zero-downtime 결정**: Option B (점검창 5분 수용). Phase 0 실습 환경이며 사용자 수 15명 제한.

```
Day 1 오전: terraform plan 작성 + 검토
Day 1 오후: 점검창 공지 → terraform apply (ElastiCache replacement) → 검증
Day 2 오전: auth-gateway / pipelines / usage-worker rollout (REDIS_URL 갱신)
```

### Batch 2 — API 표면 축소 (api)

```
Day 1: main.py docs_url, auth.py WWW-Authenticate 전수 교체
Day 2: SameSite 재검토 — SSO redirect 실환경 테스트 + 결정 문서
```

### Batch 3 — Phase 0 잔재 + data export (api + ops)

```
Day 1: jwt_rs256 deterministic kid + unit test
Day 2: ops/export 4개 스크립트 + unit test
Day 3: ops/export Dockerfile + README
```

### Wave 구성 (총 3일)

| Wave | 시점 | 작업 | 팀 |
|------|------|------|-----|
| 1 | Day 1 AM | terraform plan + API/kid 코드 시작 | devops + api |
| 2 | Day 1 PM | terraform apply (점검창) + Batch 2 commit | devops + api |
| 3 | Day 2 AM | manifest 업데이트 + rollout bundle | devops + k8s |
| 4 | Day 2 PM | Batch 3 완료 + SameSite 문서 + export 테스트 | api + ops |
| 5 | Day 3 | security iter + review drift + qa e2e | 감시 3팀 |

### 팀 구성 (6팀)

| 팀 | 역할 | 모델 |
|----|------|------|
| devops | Batch 1 주도 | sonnet |
| k8s | Batch 1 보조 (manifest + rollout) | sonnet |
| api | Batch 2 + Batch 3 코드 | sonnet |
| ops | Batch 3 export 스크립트 | sonnet |
| security | 루프 감시 + TLS/docs/WWW-Auth/kid 검증 | sonnet |
| review | drift 10/10 checklist 감시 | sonnet |
| qa | 회귀 + e2e + rediss 오버헤드 측정 | sonnet |

**debug 생략**: Phase 1a는 core 테스트 변경 작음 + 레드라인 파일 수정 없음. spot-check만 security가 대리.

## 6. 격리 전략

- 신규 worktree `.worktrees/feat-phase1a-security-hardening`
- 신규 branch `feat/phase1a-security-hardening` (main에서 분기)
- main 브랜치 OnlyOffice P2 iteration과 격리 유지
- Phase 1a 완료 후 main merge (Phase 0 merge 패턴 재사용)

## 7. 테스트/검증 전략

### Unit 테스트 신규

| 파일 | 대상 |
|------|------|
| `tests/test_deterministic_kid.py` | jwt_rs256._compute_kid — 동일 PEM 동일 kid, 길이 16, replica 모의 일치 |
| `tests/unit/test_export_chats.py` | 90d 범위, JSONL 포맷, 권한 필터 |
| `tests/unit/test_export_skills.py` | approval_status 필터, CSV 헤더 |
| `tests/unit/test_export_usage.py` | 일별 집계, Parquet 스키마 |
| `tests/unit/test_export_audit.py` | FileAuditLog 필터, PII 마스킹 |

### 회귀 테스트 (Phase 0 baseline 유지)

- core 72/72: test_viewers.py(49) + test_k8s_service.py(7) + test_shared_mounts_auth.py(8) + test_jwt_replay_protection.py(8)
- test_cookie_domain.py(6) + test_cookie_html_client.py(21): SameSite 결정 반영 재실행
- test_auth_jwt_phase0.py(11): kid 변경 영향 확인

### E2E 테스트

| CP | 검증 포인트 |
|----|------------|
| CP-11/12 jti replay | rediss 전환 후 Redis 연결 영향 없음 |
| CP-13~15 cookie | SameSite 결정 반영 |
| CP-16/17 header forgery | ingress strip 영향 없음 |
| CP-18 TTFT | 0.52s 유지 여부 (rediss 오버헤드 +50ms 이내 허용) |
| CP-21 health | WWW-Authenticate 헤더 표준 전환 확인 |

### 보안 검증 체크리스트 (security iter)

| 대상 | 명령 | 기대 |
|------|------|------|
| rediss transit | `kubectl exec pod -- redis-cli -u rediss://:<token>@host PING` | `PONG` + TLS handshake |
| 미인증 차단 | `redis-cli -u redis://host PING` (auth_token 없이) | `NOAUTH` / TLS rejected |
| /docs 차단 | `curl https://auth.skons.net/docs` | `404` |
| /openapi.json 차단 | `curl https://auth.skons.net/openapi.json` | `404` |
| WWW-Auth 표준 | `curl -i https://auth.skons.net/api/v1/auth/me` (unauth) | `WWW-Authenticate: Bearer realm="skons.net"` |
| kid 일치 | 2 replica `/auth/.well-known/jwks.json` | 동일 `kid` |
| KMS rotation | `aws kms get-key-rotation-status --key-id ...` | `KeyRotationEnabled: true` |
| export 권한 | 일반 user IAM role로 실행 | AccessDenied |

### Drift checklist 확장 (review 팀)

Phase 0 8항목 + 신규 2항목 = **10/10**:
9. **rediss:// 통신 암호화 강제** — `grep "redis://" infra/ auth-gateway/ usage-worker/` 결과 0건
10. **/docs /redoc /openapi.json 공개 노출 금지** — FastAPI(docs_url=None) 유지 verify

## 8. 완료 게이트 (Acceptance criteria)

1. [ ] terraform plan 결과 diff = rediss 전환 + KMS rotation만
2. [ ] auth-gateway pod 로그 `Redis connected (TLS)` 확인
3. [ ] `curl https://auth.skons.net/docs` → 404
4. [ ] `grep "WWW-Authenticate: Cookie" auth-gateway/app/` 결과 0건
5. [ ] 2 replicas JWKS 동일 kid 반환
6. [ ] `python ops/export/chats.py --user <uid> --since 90d` JSONL 출력
7. [ ] security iter PASS 판정
8. [ ] review drift 10/10 PASS
9. [ ] qa e2e 회귀 (CP-11~18) 전원 PASS
10. [ ] Phase 1a 합동 보고 (security + review + qa) 작성 완료

## 9. 리스크 및 대응

| 리스크 | 확률 | 영향 | 대응 |
|--------|------|------|------|
| ElastiCache replacement 오래 걸림 | 중 | 고 | Option A(신규 cluster + 전환) fallback 준비 |
| SameSite Strict 전환 시 SSO redirect 깨짐 | 중 | 중 | Lax 유지 결정 + 근거 문서 |
| rediss 오버헤드 p95 > 87ms 초과 | 낮음 | 중 | cluster mode / connection pool 조정 |
| ops/export 개인정보 노출 | 낮음 | 고 | PII 마스킹 unit test 필수 통과, IAM ReadOnly role |
| kid 변경으로 구 JWT 검증 실패 | 낮음 | 중 | RSA key는 동일, kid만 변경 → JWKS 매칭 없어도 검증 성공 (iter#8 분석) |

## 10. 후속 단계

Phase 1a 완료 → **Phase 1b 설계 사이클** 시작:
- ElastiCache HA
- 50명 sizing
- budget_gate 실체
- usage_emit 실체
- /auth/issue-jwt
- T20 refresh daemon

Phase 1b 완료 → **Phase 1c 설계 사이클**:
- FileAuditAction
- Skills governance
- Admin Dashboard parallel UIs
- DEFAULT_USER_ROLE + model access_control
- Locust cookie
- psql CI

## 11. 참조

- Phase 0 설계: `~/.gstack/projects/cation98-bedrock-ai-agent/cation98-main-design-20260412-133106.md`
- Phase 0 머지 commit: `d4d99eb`
- Phase 0 테스트 보고: `docs/qa/phase0-test-report.md`, `docs/qa/phase0-t17-qa-section.md`
- mindbase Phase 0 결과: conversation `d73ffbcf-aa9a-47de-aec2-f4ce42126d91`
- Graphiti group: `proj-bedrock-ai-agent`
