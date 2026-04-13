# Phase 1a Decision: Cookie SameSite Strict vs Lax 재검토

**Status**: DECIDED — **Lax 유지** (Phase 1c에서 Strict 재평가)
**Date**: 2026-04-13
**Deciders**: Phase 1a security hardening team

## Context

Phase 0 security iter#1에서 M-3 MEDIUM으로 기록됨. 설계 §2 원안 "SameSite=Strict" 이었으나 SSO redirect 호환성을 위해 Lax로 확정(`0df04b4`). Phase 1a에서 재검토 수행.

현재 코드: `auth-gateway/app/routers/jwt_auth.py` — `bedrock_jwt`, `bedrock_jwt_vis` 모두 `samesite="lax"`.

## Options Considered

### (A) Strict 전환
- **Pro**: CSRF 방어 최대. 모든 cross-site navigation에서 쿠키 전송 차단.
- **Con**:
  - 외부 SSO redirect flow 시 `bedrock_jwt` 미전송 → 401 loop 가능성 (SSO 공급자 → sso.skons.net → auth.skons.net callback → portal.skons.net 복귀 중 1회 이상 cross-site navigation 발생)
  - portal.skons.net ↔ auth.skons.net ↔ chat.skons.net 간 top-level GET navigation 전부 영향
  - Open WebUI 공유 URL(`chat.skons.net/c/<id>`) 외부 링크 클릭 시 401 후 로그인 리다이렉트 필요 — UX 저하

### (B) Lax 유지 (현재)
- **Pro**: SSO redirect 정상 (GET navigation 쿠키 허용)
- **Pro**: POST/PUT/DELETE 등 mutating method는 Lax에서도 쿠키 전송 안 함 → 대부분 CSRF 공격 여전히 방어됨
- **Con**: top-level GET에서 쿠키 전송 — CSRF read 공격이 이론상 가능하나 read-only 엔드포인트에 한함 (mutating endpoint는 별도 CSRF token 적용 가능)

### (C) None + Secure (cross-site 완전 허용)
- **Pro**: 모든 navigation 호환
- **Con**: CSRF 방어력 크게 감소 — Phase 0 설계 §2 위반

## Decision

**B (Lax 유지)**.

근거:
1. Phase 0 실습 환경은 15명 내부 사용자 전제 — CSRF 공격 surface 제한적
2. `scripts/verify-sso-redirect-flow.sh` 검증 스크립트 작성 완료 (로컬 실 환경 대상으로 실행 가능)
3. Strict 전환 시 SSO redirect 첫 단계에서 쿠키 전송 차단 → 401 loop 가능성 높음 — 브라우저 SameSite 정책에 의해 top-level cross-site GET에서도 Strict는 쿠키 미전송
4. 현재 mutating 엔드포인트는 JWT Bearer header 이중 검증으로 쿠키 단독 mutation 불가 — Lax 하에서도 실질적 CSRF 위험 낮음
5. Phase 1c(팀장 50명 스케일 + 외부 파트너 연동 검토) 진입 시 CSRF token double-submit 패턴 도입 후 Strict 재평가 예정

## Mitigation (Lax 하 CSRF 완화)

- POST/DELETE/PUT 엔드포인트는 현재 JWT Bearer header 추가 검증 — 쿠키만으로 mutation 불가
- 민감 API(예산 관리, Secret 접근)는 Phase 1c에서 별도 CSRF token 도입 검토
- Phase 1a 구현 범위: 현 상태 유지 (코드 변경 없음, jwt_auth.py 주석 명확화만 적용)

## Review triggers (Phase 1c 재평가 조건)

다음 중 하나 발생 시 Strict 전환 재평가:
- 팀장 50명 스케일로 사용자 규모 증가
- 외부 공급사 또는 파트너 SSO 연동 추가
- PIPA/ISMS-P 외부 감사 앞두고 compliance 요구 확대
- CSRF 공격 실증 발생 또는 보안팀 재평가 요청

## 참조
- `auth-gateway/app/routers/jwt_auth.py` — 쿠키 설정 위치 (line 71 `samesite="lax"`)
- `scripts/verify-sso-redirect-flow.sh` — SSO flow 자동 검증 스크립트
- Phase 0 security iter#1 M-3 MEDIUM 원문 (`claudedocs/lane-b-recovery-log.md` 참조)
