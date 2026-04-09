# Full Session Summary: 2026-04-09

## 완료한 작업 (10건)

### 1. RBAC pods/exec get verb 추가 (ab77dbc → c3feb45)
- 원인: Python K8s client의 connect_get_namespaced_pod_exec는 GET 요청 → RBAC에 get verb 필요
- auth-gateway 로그에서 403 Forbidden 확인, pods/exec verbs에 get 추가
- 토큰 수집 즉시 복구 (5개 Pod)

### 2. auth-url port 8000→80 (k8s_service.py)
- 원인: hub/files ingress의 auth-url이 Service port 80이 아닌 container port 8000 직접 참조
- nginx auth subrequest → connection refused → 503
- 코드 수정 + 기존 22개 ingress kubectl annotate 패치

### 3. files-auth-check X-Original-URL 파싱 (01b3660)
- 원인: nginx-ingress는 X-Original-URL(full URL) 전송, 코드는 X-Original-URI(path) 기대
- URI 헤더 비어서 pod_owner="" → 403
- urlparse로 X-Original-URL에서 path 추출

### 4. 로그인 공지 14시 변경 (01b3660)
- ~12:30 → ~14:00, 순단 가능성 + 14시 이후 사용 권장

### 5. 전용 노드 리소스 정책 (DB)
- 정병오(N1001063), 김광우(N1001064): cpu 500m→1800m, mem 1536Mi→6Gi
- m5.large allocatable (1930m/7068Mi) 대비 93% 활용

### 6. auth-gateway + container-image 빌드/배포
- hotfix-20260409 태그, ECR push, rollout restart

### 7. 구 이미지 사용자 Pod 5개 제거
- 전원 Pod 삭제 + 세션 terminated, 재로그인 시 새 이미지 적용

### 8. ingress-nginx hard anti-affinity
- preferred → required 변경, 동일 노드 몰림 방지

### 9. 시스템 노드 3→2 축소
- system-node-large nodegroup desired 3→2

### 10. pair 정책 기록
- CLAUDE.md Infrastructure Design Constraints 섹션
- auto memory infra_system_node_pair.md

## 프로덕션 이미지
- auth-gateway: hotfix-20260409 (sha256:777fe508...)
- claude-code-terminal: hotfix-20260409

## Git 커밋
- c3feb45: RBAC pods/exec get verb
- 01b3660: auth-check X-Original-URL + 공지 변경
- 77f36f1: CLAUDE.md pair 정책 기록

## 핵심 교훈
- Python K8s client connect_get_* = GET 요청 → RBAC get verb 필요 (kubectl exec는 POST = create)
- nginx-ingress auth-url: X-Original-URL(full URL)을 보냄, X-Original-URI 아님
- K8s Service port vs container targetPort 구분 필수 (auth-url에서 service port 사용)
