# Session: 2026-04-09 — RBAC pods/exec get verb 수정

## 문제
Admin Dashboard /usage 페이지에서 토큰 사용량이 전혀 표시되지 않음.

## 원인
`infra/k8s/platform/rbac.yaml`의 `pods/exec` 리소스에 `create` verb만 있었음.
auth-gateway의 `_collect_tokens_from_pod()`는 Python K8s client의
`connect_get_namespaced_pod_exec` (GET 요청)을 사용 → `get` verb 필요.
모든 Pod exec가 403 Forbidden으로 실패, 토큰이 (0,0)으로 silent fail.

## 수정 (ab77dbc)
- `pods/exec` verbs: `["create"]` → `["create", "get"]`
- `kubectl apply` 즉시 적용, 5개 Pod 모두 토큰 수집 복구 확인

## 교훈
- Python K8s client의 `connect_get_*` 메서드는 GET 요청 → RBAC `get` verb 필요
- `connect_post_*` 메서드는 POST 요청 → RBAC `create` verb 필요
- `kubectl exec`는 POST 기반이므로 `create`만으로 동작하지만, Python client는 다름
- 토큰 수집 실패는 (0,0) silent fail — 에러가 로그에만 남고 API 응답은 200 OK
