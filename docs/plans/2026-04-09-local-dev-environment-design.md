# Local Development Environment Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Docker Desktop K8s에서 프로덕션과 거의 동일한 전체 시스템을 로컬 Mac에서 구동

**Architecture:** Docker Desktop K8s 단일 노드에 platform/claude-sessions/ingress-nginx 네임스페이스 구성. PostgreSQL+Redis는 K8s Pod로, AWS 자격증명은 Secret 마운트, SSO는 TEST 계정 우회.

**Tech Stack:** Docker Desktop K8s, kubectl, Docker build (로컬 이미지)

---

## 구성 요소

| 서비스 | 프로덕션 | 로컬 |
|--------|---------|------|
| auth-gateway | ECR + EKS | 로컬 빌드 + Docker Desktop K8s |
| 사용자 Pod | ECR + EKS (동적 생성) | 로컬 빌드 + 수동/자동 생성 |
| PostgreSQL | RDS | K8s Pod (5432) |
| Redis | ElastiCache | K8s Pod (6379) |
| OnlyOffice | K8s Pod | K8s Pod |
| Ingress | nginx Ingress Controller | nginx Ingress Controller |
| EFS | AWS EFS | hostPath (~/.bedrock-local-data/) |
| SSO | sso.skons.net | TEST 계정 우회 |
| Claude API | Bedrock (STS) | Bedrock (~/.aws/ 마운트) |

## 구동 순서

```bash
# 0. 컨텍스트 전환
kubectl config use-context docker-desktop

# 1. 네임스페이스
kubectl apply -f infra/local-dev/00-namespaces.yaml

# 2. PostgreSQL + Redis
kubectl apply -f infra/local-dev/01-postgresql.yaml
kubectl apply -f infra/local-dev/02-redis.yaml

# 3. Secrets (JWT, DB, AWS)
kubectl apply -f infra/local-dev/03-secrets.yaml

# 4. RBAC + ServiceAccount
kubectl apply -f infra/local-dev/04-rbac.yaml

# 5. Auth Gateway
docker build -t bedrock-claude/auth-gateway:local auth-gateway/
kubectl apply -f infra/local-dev/05-auth-gateway.yaml

# 6. Ingress Controller + 규칙
kubectl apply -f infra/local-dev/06-ingress.yaml

# 7. OnlyOffice (선택)
kubectl apply -f infra/local-dev/07-onlyoffice.yaml

# 8. 사용자 Pod (테스트)
docker build -t bedrock-claude/claude-code-terminal:local container-image/
kubectl apply -f infra/local-dev/08-test-user-pod.yaml
```

## 접속

| 서비스 | URL |
|--------|-----|
| Hub | http://localhost/hub/claude-terminal-test001/ |
| 터미널 | http://localhost/terminal/claude-terminal-test001/ |
| 파일관리 | http://localhost/files/claude-terminal-test001/ |
| Admin Dashboard | http://localhost:3000 (npm run dev) |
| Auth Gateway API | http://localhost/api/v1/ |

## 개발 워크플로

1. 코드 수정
2. `docker build -t bedrock-claude/auth-gateway:local auth-gateway/`
3. `kubectl rollout restart deployment/auth-gateway -n platform`
4. 브라우저 확인
5. 검증 후 프로덕션 배포 (`--platform linux/amd64` + ECR push)
