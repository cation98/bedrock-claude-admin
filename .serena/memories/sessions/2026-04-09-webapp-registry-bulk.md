# Session: 웹앱 레지스트리 일괄 등록 + Hub UI 수정 (2026-04-09)

## Summary
32명 사용자 51개 앱을 .webapp-registry.json에 일괄 등록하고, Hub UI에서 앱 관리 기능을 구현함.

## Commits (6건)
1. `a0a0fa8` — 웹앱 카드 클릭 탭 전환 + Python entrypoint 자동 감지
2. `f6df6b6` — 내 웹앱 목록에 로컬 레지스트리 앱도 표시 (platform API + local merge)
3. `f1beea5` — 다중 포트 프록시 (_port 쿼리 파라미터) + UI 개선
4. `5b704aa` — 앱 실행 시 자동 새 탭 열기 + 토스트 피드백
5. `895213d` — 포트 충돌 방지 (레지스트리 기반 할당)
6. `49eade0` — 동일 앱 재실행 시 기존 프로세스 종료 후 같은 포트 재시작

## Key Changes
- **fileserver.py**: loadMyApps()가 platform API + local registry 병합, startApp()에 자동 탭 열기/포트 할당/재시작 로직
- **app_proxy.py**: `_port` 쿼리 파라미터로 3000-3100 포트 프록시 오버라이드
- **EFS**: 32명 사용자별 .webapp-registry.json 생성 (entrypoint 포함)
- **DB**: 96명 전체 can_deploy_apps=True, 정병오/김광우 infra_policy→standard

## TODO (다음 세션)
1. Hub에 "공유하기" 버튼 추가 → POST /api/v1/apps/deploy 연결
2. 배포 시 ACL 범위 선택 모달 (user/team/region/job/company)
3. /webapp-login 페이지 구현 (공유 앱 SSO 로그인)
4. deployed 상태 앱의 "접근 관리"/"삭제" 버튼 연결 확인
