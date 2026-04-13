# Phase 1a Security Iter #10 — Internal Audit 결과

**Date**: 2026-04-13  
**Branch**: feat/phase1a-security-hardening  
**Commits reviewed**: 066febf..e457441 (총 18 commits, Task 1~14)  
**Auditor**: 감시자 역할 (수정 없음, findings만 보고)

---

## Summary

Phase 1a Batch 1+2+3 구현(Task 1~14) 완료 후 9-checkpoint 내부 security audit 실시.
`auth-gateway` pod 2개 (676f6f748c-m6x7t, 676f6f748c-zwgqg) 대상으로 live 검증 수행.

**전체 판정**: ~~CONDITIONAL PASS — 7/9 PASS, 2개 non-blocking FAIL (이미지 미재배포)~~  
**최종 판정 (재배포 후)**: **FULL PASS — 9/9 PASS** (ECR 재빌드 + rollout 완료, 2026-04-13)

---

## Results

| # | 항목 | 검증 방법 | 결과 | 비고 |
|---|------|---------|------|------|
| 1 | rediss transit | pod exec: REDIS_URL prefix 확인 | **PASS** | `rediss://:***@master.bedrock-claude-redis-tls…` |
| 2 | 미인증 Redis 차단 | pod exec: auth_token 없이 PING | **PASS** | `AuthenticationError: Authentication required.` |
| 3 | /docs 차단 | `curl https://auth.skons.net/docs` | **PASS** | HTTP 404 반환 |
| 4 | /redoc 차단 | `curl https://auth.skons.net/redoc` | **PASS** | HTTP 404 반환 |
| 5 | /openapi.json 차단 | `curl https://auth.skons.net/openapi.json` | **PASS** | HTTP 404 반환 |
| 6 | WWW-Auth Bearer | pod exec: `/api/v1/auth/webui-verify` 401 헤더 | **FAIL** | `WWW-Authenticate: Cookie` (expected: `Bearer realm="skons.net"`) |
| 7 | 2 replica kid 일치 | pod exec: JWKS 각 replica 조회 | **FAIL** | pod1 kid=`9335a31da0776d43`, pod2 kid=`5d444b53c7598e30` |
| 8 | KMS rotation | AWS KMS API | **PASS** | `KeyRotationEnabled: true, RotationPeriodInDays: 365, NextRotationDate: 2027-04-09` |
| 9 | export 스크립트 동작 | `pytest tests/unit/ -q` + `--help` 4종 | **PASS** | `15 passed in 0.18s` + 각 스크립트 usage 정상 출력 |

---

## 체크 상세 로그

### Check 1 — rediss transit (PASS)
```
REDIS_URL: rediss://:***@master.bedrock-claude-redis-tls.khpawo.apn2.cache.amazonaws.com:6379/0
CHECK 1: PASS - rediss:// TLS transit enabled
```

### Check 2 — 미인증 Redis 차단 (PASS)
```
CHECK 2: PASS - blocked (AuthenticationError): Authentication required.
```
auth_token 없이 TLS 연결 시도 시 `redis.exceptions.AuthenticationError` 발생 확인.

### Check 3/4/5 — /docs /redoc /openapi.json 차단 (PASS)
```
HTTP/2 404  (server: nginx/1.28.0)
content-type: text/html; charset=utf-8
body: <title>Not Found</title>
```
FastAPI `docs_url/redoc_url/openapi_url=None` 설정 적용 확인. nginx가 404 반환.

### Check 6 — WWW-Authenticate Bearer (FAIL)

**기대값**: `WWW-Authenticate: Bearer realm="skons.net"`  
**실제값**: `WWW-Authenticate: Cookie`

```
HTTP 401
www-authenticate: Cookie
```

**Root cause**: 소스 트리(e38905e 커밋)에는 `Bearer realm="skons.net"` 가 올바르게 구현되어 있으나,
현재 운영 중인 container image가 해당 커밋 이후 재빌드/재배포되지 않았음.

```
# 배포된 이미지 /app/app/routers/auth.py:529
headers={"WWW-Authenticate": "Cookie"},   ← 이전 코드

# 소스 트리 (e38905e)
headers={"WWW-Authenticate": 'Bearer realm="skons.net"'},   ← 수정된 코드
```

**Impact**: Low — 기능적 인증 차단은 정상 작동. WWW-Authenticate 헤더는 RFC 7235 표준 준수 목적이므로
보안 기능 결함이 아닌 표준 준수 미완.  
**Required action**: 이미지 재빌드 후 재배포 필요.

### Check 7 — 2 replica kid 일치 (FAIL)

**기대값**: 두 pod 동일한 kid  
**실제값**: pod1=`9335a31da0776d43`, pod2=`5d444b53c7598e30`

```
pod1 JWKS: "kid": "9335a31da0776d43", "n": "5D49Ar_MCF06..."
pod2 JWKS: "kid": "5d444b53c7598e30", "n": "5D49Ar_MCF06..."
```

**핵심 발견**: 두 pod의 `n` (RSA public key modulus)이 동일 — K8s Secret에서 같은 키를 로드함.  
그러나 `kid` 값이 다름.

**Root cause**: 배포된 이미지의 `jwt_rs256.py` 는 여전히 `secrets.token_hex(8)` 으로 kid 생성:
```python
# 배포된 이미지 내 /app/app/core/jwt_rs256.py:152
_key_id = secrets.token_hex(8)   ← 재시작마다 랜덤 생성

# 소스 트리 (660a941 커밋) — 결정론적 kid
_key_id = _compute_kid(pem_bytes)  # SHA256(n||e)[:16]
```

Pod 시작 시 각자 독립적으로 random kid 생성 → kid mismatch.

**Impact**: Medium — JWT 검증 시 kid 불일치로 특정 pod에서 발급된 토큰이 다른 pod에서 검증 실패 가능.
로드밸런서가 두 pod에 분산하므로 실제 서비스에서 간헐적 401 발생 위험.  
**Required action**: 이미지 재빌드 후 `rollout restart` 필요. (Check 6과 함께 1회 재빌드로 해결 가능)

### Check 8 — KMS Rotation (PASS)
```json
{
  "KeyRotationEnabled": true,
  "RotationPeriodInDays": 365,
  "NextRotationDate": "2027-04-09T12:44:45.839000+09:00"
}
```
Key ID: `bc47d786-64b9-42ae-8d03-58374253dd23` (S3 Vault KMS key, ap-northeast-2)

### Check 9 — export 스크립트 동작 (PASS)

```
# Unit tests
15 passed in 0.18s

# --help 출력 (각 스크립트 정상)
chats.py:  usage: chats.py [-h] --user USER [--since SINCE] [--output OUTPUT]
skills.py: usage: skills.py [-h] [--status {approved,pending,rejected}] [--output OUTPUT]
usage.py:  usage: usage.py [-h] --since SINCE [--output OUTPUT]
audit.py:  usage: audit.py [-h] [--since-days SINCE_DAYS] [--output OUTPUT]
```

---

## 주요 발견

### APPROVED 항목 (7/9)
- Redis transit encryption (`rediss://`) — ElastiCache TLS 전환 완료
- Redis 미인증 차단 — `auth_token` 필수화 확인
- API 문서 엔드포인트 차단 — /docs /redoc /openapi.json 모두 404
- KMS rotation 활성화 — 365일 주기 자동 교체 구성
- export 스크립트 전체 — 15 unit tests PASS + 4종 CLI 정상 동작

### 잔여 이슈 (2건, non-blocking)

| 이슈 | 심각도 | 원인 | 조치 |
|------|--------|------|------|
| Check 6: WWW-Authenticate: Cookie (RFC 7235 미준수) | Low | 이미지 재배포 누락 | 이미지 재빌드 + rollout restart |
| Check 7: 2 replica kid 불일치 (간헐적 401 위험) | Medium | 이미지 재배포 누락 (secrets.token_hex) | 이미지 재빌드 + rollout restart |

두 이슈 모두 **동일한 원인** (이미지 재빌드 미완료)이며 **1회 재배포로 동시 해결** 가능.

---

## Phase 1a 완료 판정

### CONDITIONAL PASS

**이유**:
- 7/9 보안 체크포인트 PASS
- 2개 FAIL 모두 코드 구현 결함이 아닌 **배포 누락** 으로 인한 것
- 소스 트리(코드)는 모든 9개 항목을 올바르게 구현함
- Check 7 (kid mismatch)은 간헐적 인증 실패를 유발할 수 있는 Medium 이슈이나, 동일 K8s Secret을 사용하므로 키 자체는 안전

**Phase 1a → Phase 1b 이관 조건**:
1. 이미지 재빌드 (`docker build` + ECR push)
2. `kubectl rollout restart deployment/auth-gateway -n platform`
3. 재시작 후 Check 6 + Check 7 재검증 수행

---

## Phase 1b 이관 백로그 (audit 발견 항목)

| 항목 | 우선순위 | 내용 |
|------|--------|------|
| **이미지 재배포** | P0 (즉시) | Check 6+7 해결 — 재빌드 후 rollout restart |
| `/api/v1/auth/me` 외부 노출 여부 확인 | P1 | ingress에서 외부 접근 시 404 반환 — 의도적 차단인지 ingress 설정 확인 필요 |
| JWKS rotation 정책 문서화 | P2 | kid deterministic 알고리즘(SHA256 n\|\|e) 운영 문서 작성 |
| Check 6 재검증 자동화 | P3 | CI/CD에 `curl + grep WWW-Authenticate: Bearer` 게이트 추가 |

---

## Post-rebuild Verification (2026-04-13 KST)

ECR 재빌드 digest: `sha256:9ca9d3a250ce1ac0746d28ceb31d60c3c1a2e6705a18d5a33c536f5ec8585817`  
Rollout 완료 시각: 2026-04-13 13:34 KST (약 90초 소요)  
재빌드 베이스 커밋: `9d37060` (feat/phase1a-security-hardening HEAD)

| # | 항목 | 재검증 결과 | 실제 응답 |
|---|------|-----------|---------|
| 6 | WWW-Authenticate Bearer | **PASS** | `www-authenticate: Bearer realm="skons.net"` |
| 7 | 2 replica kid 일치 | **PASS** | pod1=`9973b029e75cbd06`, pod2=`9973b029e75cbd06` |

**최종 판정**: 9/9 PASS — Phase 1a security audit **FULL PASS**

### 재검증 상세

**Check 6** — `curl -sk -i https://claude.skons.net/api/v1/auth/me` (no auth token):
```
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer realm="skons.net"
```

**Check 7** — 각 replica JWKS `/auth/.well-known/jwks.json` 조회:
```
pod/auth-gateway-5c6dc795b4-r5pv2: kid = 9973b029e75cbd06
pod/auth-gateway-5c6dc795b4-xnj7m: kid = 9973b029e75cbd06
```
동일 K8s Secret PEM → SHA256(n||e)[:16] 결정론적 kid 일치 확인.

---

## 참고: 18 commits 구조

```
281f10c feat(phase1a-batch1): ElastiCache HA + transit encryption + auth_token
327d96c fix(phase1a-batch1): ElastiCache multi_az + lifecycle auth_token + retention 7
dfb613a feat(phase1a-batch1): redis-auth-token Secret manifest 가이드
003ecc5 feat(phase1a-batch1): REDIS_URL rediss:// 전환 + REDIS_AUTH_TOKEN secretKeyRef
462e642 fix(phase1a-batch1): Redis URL log 마스킹 — auth_token 평문 노출 차단
098af52 feat(phase1a-batch1): KMS S3 Vault key deletion_window 30일 표준화
e38905e feat(phase1a-batch2): FastAPI docs_url/redoc_url/openapi_url=None          ← Check 3/4/5
c3882b0 feat(phase1a-batch2): WWW-Authenticate Bearer realm='skons.net' 표준 전환  ← Check 6 (미배포)
1a598e9 docs(phase1a-batch2): SameSite Lax 유지 결정 + SSO 검증 스크립트
660a941 feat(phase1a-batch3): deterministic kid = SHA256(n||e)[:16]                ← Check 7 (미배포)
d370c7f fix(phase1a-batch3): RSA isinstance check + _load_key_from_pem signature
6f445ea feat(phase1a-batch3): ops/export 공통 유틸 (mask_pii + db_session + resolve_username)
c5787b6 feat(phase1a-batch3): ops/export/chats.py — Open WebUI chat JSONL export
247a671 feat(phase1a-batch3): ops/export/skills.py — skills CSV export
2edee2d feat(phase1a-batch3): ops/export/usage.py — token_usage_daily Parquet export
d0b6619 feat(phase1a-batch3): ops/export/audit.py — FileAuditLog JSONL + PII masking
e457441 feat(phase1a-batch3): ops/export Dockerfile + README
```
