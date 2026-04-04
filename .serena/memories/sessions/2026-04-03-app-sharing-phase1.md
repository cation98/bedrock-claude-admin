# Session: App Sharing Phase 1 — Full Lifecycle (2026-04-03)

## What was accomplished
Phase 1 앱 공유 모드 완전 사이클: 디자인 → 리뷰 → 구현 → 테스트 → 코드리뷰 → 배포

## Key deliverables
- PR #2 merged to main (squash)
- Auth-gateway deployed to EKS (image: phase1-app-sharing-20260403-0641)
- Admin dashboard deployed to Amplify (job #62)

## Architecture decisions
- 3단계 배포 모델: 개발(Pod 프리뷰) → 공유(Pod 멀티포트) → 상용(Phase 2 경량 Pod)
- Auth-gateway reverse proxy 방식 (K8s Service 동적 생성 대신)
- visibility: private/company (team은 SSO 그룹 매핑 조사 후 Phase 3)
- Pod 유휴 1시간 정책 유지 + 오프라인 안내 페이지
- SSRF 방지: 포트 3000-9999 범위 + 블랙리스트 (6379, 5432, 3306 등)

## Bugs found and fixed
- Codex: gallery API 응답 형식 불일치 (bare list vs {apps:[...]})
- Codex: SQLAlchemy 단일 컬럼 쿼리 → 모델 전체 쿼리로 변경
- Claude adversarial: SSRF 포트 화이트리스트, asyncio deprecation, admin 폴백 보안
- QA: SSRF 6379 통과 → 블랙리스트 방식으로 강화

## Workflow used
/office-hours → /plan-eng-review → subagent-driven-development (5 parallel agents) → /review → /codex review → /qa → /ship → deploy

## Phase 2 backlog
- 경량 Pod 빈패킹 (node:alpine, python:slim)
- 세션 영속성 (#1 사용자 불편)
- Pod 웹서버 자동 감지 (ss -tlnp cron)
- team visibility (SSO 그룹 매핑)
- 프록시 DB 쿼리 캐싱
