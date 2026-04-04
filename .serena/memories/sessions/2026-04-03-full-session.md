# Full Session: 2026-04-03 — Two Features + Hotfix

## Delivered

### PR #2: Phase 1 앱 공유 모드 (MERGED + DEPLOYED)
- visibility(private/company), multi-port, view counting, offline page
- SSRF 포트 블랙리스트, gallery 접근제어
- 16 tests, Codex 크로스리뷰 3 bugs fixed

### PR #3: Telegram 현장 데이터 수집 + 파일 정책 (MERGED + DEPLOYED)
- Survey models (template/assignment/response) + API CRUD
- Telegram 봇 확장 523→979줄 (inline keyboard, S3 photo upload, state machine)
- Storage cleanup cron (24h, storage_retention 기반)
- Admin dashboard surveys page + sidebar
- 31 tests total, Codex 크로스리뷰 3 bugs fixed

### Hotfix: claude-code-terminal 이미지 재빌드
- 원인: 이미지에 auth-gateway 코드가 실수로 포함됨
- 증상: Pod 시작 시 CREATE TABLE → ReadOnly DB → Error
- 수정: container-image/에서 정상 재빌드 (fix-no-authgw-20260403-0849)
- K8S_POD_IMAGE 환경변수 업데이트

## Architecture Decisions
- 3단계 배포 모델: 개발(Pod 프리뷰) → 공유(Pod 멀티포트) → 상용(Phase 2 경량 Pod)
- Telegram survey: 즉시 응답 + asyncio background S3 업로드 (webhook 5초 타임아웃 방지)
- Storage cleanup: dry-run 기본값, Telegram 알림 3일 전
- Pod에 DATABASE_URL로 ReadOnly Replica 주입 중 → 이름 변경 검토 필요

## Image Tags (현재 프로덕션)
- auth-gateway: telegram-survey-20260403-0826
- claude-code-terminal: fix-no-authgw-20260403-0849
- K8S_POD_IMAGE env var updated to new terminal image

## Deferred (다음 세션)
- 외부 API 화이트리스트 (URL/도메인 레벨)
- S3/Athena 직접 쿼리 (Pod에서)
- 전용 Telegram 봇 생성
- Phase 2 경량 Pod 배포
- DATABASE_URL → SAFETY_DATABASE_URL 이름 변경
