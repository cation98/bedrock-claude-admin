# Session: 2026-04-09 — auth-url port 8000→80 수정 + 전용 노드 리소스 정책

## 수정 1: Hub/Files Ingress auth-url 포트 오류
- **문제**: hub/files ingress의 auth-url이 `auth-gateway.platform.svc.cluster.local:8000` 참조
- K8s Service는 port 80 (→ targetPort 8000)만 노출 → nginx auth subrequest가 connection refused → 503
- **결과**: 모든 사용자의 Hub 페이지 + 파일 탐색기 접근 불가, 로그인 시 "접속 준비 중" 무한 대기
- **수정**: k8s_service.py에서 `:8000` 제거 → port 80 기본 사용
- 기존 22개 ingress 즉시 kubectl annotate로 패치

## 수정 2: 전용 노드 사용자 리소스 정책
- 정병오(N1001063), 김광우(N1001064): presenter 노드(m5.large) 전용
- 기존: cpu_request=500m, mem=1536Mi (노드 대비 26%)
- 변경: cpu_request=1800m, mem=6Gi (노드 allocatable 1930m/7068Mi 대비 93%)
- 정병오 Pod 삭제 + 세션 terminated 처리 (재로그인 시 새 정책 적용)

## 수정 3: RBAC pods/exec get verb (별도 커밋 ab77dbc)
- Python K8s client의 connect_get_namespaced_pod_exec는 GET 요청 → get verb 필요
- 토큰 수집 403 Forbidden 해결

## 파일 변경
- auth-gateway/app/services/k8s_service.py: auth-url 포트 수정 (line 582, 627)
- infra/k8s/platform/rbac.yaml: pods/exec get verb 추가 (커밋 완료)
