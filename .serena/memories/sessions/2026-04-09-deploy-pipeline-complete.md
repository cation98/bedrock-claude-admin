# Session: 배포 파이프라인 완성 + Username 프라이버시 (2026-04-09)

## Summary
웹앱 공유/배포 end-to-end 파이프라인 구현. username 대신 SHA-256 slug 기반 URL. QA 2라운드 진행.

## 오늘 전체 작업 흐름 (3개 세션)

### Session 1: webapp-registry-bulk
- 32명 51개 앱 .webapp-registry.json 일괄 등록
- Hub 카드 클릭 버그 수정 (switchHubTab)
- Python entrypoint 자동 감지 (app.py/main.py)
- 96명 can_deploy_apps=True

### Session 2: webapp-multiport-proxy
- 근본 원인 발견: Ingress /app/{pod}/ → Pod:3000 하드코딩
- fileserver /webapp/{port}/ 리버스 프록시 구현
- /proc/net/tcp 기반 포트 스캔 + 프로세스 종료 (fuser/ss 제거)
- CWD 기반 실행 감지
- 앱 삭제 확인 강화 (이름 입력 필수)
- 401 커스텀 에러 페이지 (files-unauthorized.html)

### Session 3: app-share-deploy-ui
- "공유" 버튼 + 배포 모달 (공개범위 + ACL 사용자 선택)
- deploy 엔드포인트 → K8s Pod/Service/Ingress 생성 연결
- prepare-deploy API (앱 코드 → deployed/ 복사)
- Username 프라이버시: app_slug (SHA-256 8자리)
- auth-check: slug 기반 URL 파싱
- undeploy: K8s 리소스 삭제 추가
- QA 보강: slug 충돌 retry, path traversal 방지, app_port 전달

## Key Decisions
- fileserver를 리버스 프록시로 활용 (Ingress/K8s 변경 없이 다중 포트 지원)
- /proc/net/tcp + inode 매칭 (ss/fuser 없는 minimal 컨테이너 대응)
- slug = SHA-256(username)[:8] — 4.3B 가능값, collision retry 포함
- 배포 = prepare-deploy(코드복사) → deploy(DB+K8s) 2단계

## TODO (다음 세션)
- 배포 실제 테스트 (Hub에서 공유 → K8s Pod 생성 확인)
- NetworkPolicy 라벨 수정 (claude-app vs claude-webapp)
- 중복 deploy 로직 리팩터링 (router vs service)
- 개인 Hub URL의 username 노출은 이번 범위 외 (향후)
