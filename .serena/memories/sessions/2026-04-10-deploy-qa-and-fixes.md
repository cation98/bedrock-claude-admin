# Session: 배포 QA 테스트 + 버그 수정 + Deployment 변환 (2026-04-10)

## Summary
배포 파이프라인 E2E QA 테스트 → 5건 버그 발견/수정 → Pod→Deployment 변환 → 앱 URL 정상 접근 확인.

## QA 발견 버그 및 수정
1. **ServiceAccount 누락** → `infra/k8s/webapp/service-account.yaml` 생성
2. **soft-delete 재배포 충돌** → 기존 레코드 재활성화 로직 추가
3. **Ingress configuration-snippet 차단** → snippet 제거, 앱 미들웨어 위임
4. **auth-url 포트 8000→80** → Service 노출 포트와 일치
5. **SSL redirect 무한 루프** → ALB TLS 종료이므로 force-ssl-redirect 제거
6. **X-Original-URI 누락** → X-Original-URL 폴백 파싱 추가
7. **Pod→Deployment 변환** → 자동 복구 보장 + RBAC apps/deployments 권한 추가

## 최종 동작 확인
- Deployment `app-7f43da45-tbm-dashboard` Running ✅
- Service ClusterIP:3000 ✅
- Ingress `/apps/7f43da45/tbm-dashboard/` ✅
- 앱 HTML 정상 반환 ✅
- slug 기반 URL (사번 비노출) ✅
- 24/7 상시 운영 (Deployment replicas=1) ✅

## Key Learnings
- ALB→nginx Ingress 구조에서 force-ssl-redirect는 무한 308 루프 유발
- nginx-ingress auth-url은 K8s Service port(80)로 접근해야 함 (targetPort 8000 아님)
- nginx-ingress는 X-Original-URL을 보내지만 X-Original-URI는 보내지 않을 수 있음
- configuration-snippet/server-snippet은 nginx admission webhook에서 차단됨
- Deployment 생성에는 ClusterRole에 apps/deployments 권한 필요

## TODO (다음 세션)
- Hub에서 배포된 앱 접근 관리 UI 테스트 (ACL 추가/해제)
- 다른 사용자로 배포 앱 접근 시 webapp-login → ACL 검증 테스트
- NetworkPolicy 라벨 수정 (claude-app vs claude-webapp)
- Hub "공유 받은 앱" 목록에 공유받은 앱 표시 확인
