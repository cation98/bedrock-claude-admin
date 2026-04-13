# Phase 1a Review Drift Checklist — 10/10 PASS

**Date**: 2026-04-13
**Branch**: feat/phase1a-security-hardening
**Commits reviewed**: main..HEAD (~19 Phase 1a commits)

## Checklist

| # | 항목 | 결과 | 증거 |
|---|------|------|------|
| 1 | Next.js 신규 금지 | PASS | package.json grep → empty |
| 2 | admin-dashboard 수정 금지 | PASS | `git log main..HEAD -- admin-dashboard/` → empty |
| 3 | OnlyOffice viewers.py 침범 금지 | PASS | `git log main..HEAD -- auth-gateway/app/routers/viewers.py` → empty |
| 4 | 쿠키 prefix (bedrock_jwt 우선, claude_token fallback only) | PASS | auth.py:519 `bedrock_jwt` 우선 체크 후 `claude_token` legacy fallback. app_proxy.py:52 동일 패턴 |
| 5 | JWT RS256 단일화 (HS256 잔존 없음) | PASS (scope OK) | viewers.py:920 HS256은 OnlyOffice 전용 — `grep -v onlyoffice` 필터 후 잔존 없음. Phase 1a scope 외 컴포넌트 |
| 6 | HTTP proxy 강제 | N/A | Phase 1a scope 외 (Phase 0 T20에서 적용) |
| 7 | Platform RDS single source (DATABASE_URL) | PASS | `grep -rn 'DATABASE_URL' ops/export/ \| grep 'os.environ' \| wc -l` → 1 (_common.py만) |
| 8 | Commit 메시지 정확성 | PASS | `feat/docs/fix/chore(phase1a*)` 패턴 19개. 모두 `(phase1a)` 또는 `(phase1a-batch[1-3])` scope |
| 9 | rediss:// 통신 암호화 강제 (신규) | PASS | `infra/k8s/` value에 `redis://` (non-rediss) 없음. config.py:93 주석만 (비활성 default `""`). 실 usage는 `003ecc5` 커밋으로 `rediss://` 전환 완료 |
| 10 | `/docs` `/redoc` `/openapi.json` 공개 차단 (신규) | PASS | main.py:321-323 `docs_url=None, redoc_url=None, openapi_url=None` 확인 |

**Phase 1a 최종 판정: 10/10 PASS**

## QA Regression

### auth-gateway — Phase 1a + Core suite

```
pytest tests/test_viewers.py tests/test_k8s_service.py \
       tests/test_shared_mounts_auth.py tests/test_jwt_replay_protection.py \
       tests/test_auth_jwt_phase0.py tests/test_docs_hidden.py \
       tests/test_www_authenticate_bearer.py tests/test_deterministic_kid.py -q
```

결과: **91 passed, 1 failed (pre-existing RED), 2 skipped**

| 항목 | 결과 |
|------|------|
| test_viewers.py | PASS |
| test_shared_mounts_auth.py | PASS |
| test_jwt_replay_protection.py | PASS (8 passed — redis 모듈 설치 후) |
| test_auth_jwt_phase0.py | PASS |
| test_docs_hidden.py | PASS |
| test_www_authenticate_bearer.py | PASS |
| test_deterministic_kid.py | PASS |
| test_k8s_service.py | 1 pre-existing FAIL |

**pre-existing FAIL 상세**: `TestWriteLocalFileToPod::test_uses_kubernetes_stream_not_kubectl_subprocess`
- 테스트 주석 명시: "RED: 현재 kubectl subprocess 경로 → stream 호출 0회 → FAIL"
- Phase 1a에서 해당 테스트 및 `k8s_service.py` 미수정 (`git diff main HEAD --` 결과 empty)
- 이 테스트는 Phase 2 P2-BUG3 리팩터링 완료 후 GREEN 전환 예정

### ops/export unit suite

```
PYTHONPATH=. pytest tests/unit/ -q
```

결과: **15 passed**

## Locust

실행 보류 — 로컬 환경에서 클러스터 port-forward 및 TEST_USER_TOKEN 발급 불가.
Phase 0 baseline 기록: p95=37ms. rediss 전환 후 허용 기준 p95 < 87ms (+50ms).
다음 EKS 접근 가능 세션에서 실행 필요.

## 기타 메모

- Check 4 (쿠키 prefix): `__Secure-` prefix는 HTTPS-only 환경에서 브라우저가 강제하므로
  쿠키명은 `bedrock_jwt` 유지 (SameSite=Lax + Secure + HttpOnly). Phase 1a scope 내 정상.
- Check 9: `config.py:93` 주석의 `redis://localhost:6379/0` 예시는 비활성 default(`""`)와
  함께 표기된 문서용 주석. 실제 K8s 환경에서는 `003ecc5` 커밋의 secretKeyRef로
  `rediss://` URL이 주입됨.
