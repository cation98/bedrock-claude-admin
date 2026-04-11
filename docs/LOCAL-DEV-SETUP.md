# 로컬 개발환경 구축 가이드 (macOS)

Docker Desktop K8s에서 프로덕션과 동일한 전체 시스템을 로컬에서 구동합니다.

## 사전 요구사항

| 항목 | 설치 방법 | 확인 명령 |
|------|---------|---------|
| Docker Desktop | https://docker.com/products/docker-desktop | `docker version` |
| Docker Desktop K8s | Docker Desktop → Settings → Kubernetes → Enable | `kubectl --context=docker-desktop get nodes` |
| kubectl | Docker Desktop에 포함 | `kubectl version --client` |
| AWS CLI | `brew install awscli` | `aws --version` |
| AWS 자격증명 | `aws configure` 또는 SSO | `aws sts get-caller-identity` |
| Git | macOS 기본 | `git --version` |

## 구조

```
Docker Desktop K8s (단일 노드)
├── platform 네임스페이스
│   ├── auth-gateway (FastAPI) ← 로컬 빌드 이미지
│   ├── local-db (PostgreSQL 16)
│   └── redis (Redis 7)
├── claude-sessions 네임스페이스
│   ├── claude-terminal-test001 (사용자 Pod) ← 로컬 빌드 이미지
│   └── onlyoffice (DocumentServer)
└── ingress-nginx 네임스페이스
    └── nginx-ingress-controller
```

## 빠른 시작 (원클릭)

```bash
# 1. 리포지토리 클론
git clone git@github.com:cation98/bedrock-ai-agent.git
cd bedrock-ai-agent

# 2. 전체 구동
./scripts/local-dev-up.sh

# 3. 접속
open http://localhost/
# 로그인: TEST001 / test2026
```

## 수동 구동 (단계별 학습)

### Step 0: K8s 컨텍스트 전환

```bash
# 현재 컨텍스트 확인
kubectl config current-context

# 로컬 Docker Desktop으로 전환
kubectl config use-context docker-desktop

# 확인
kubectl get nodes
# NAME             STATUS   ROLES           AGE   VERSION
# docker-desktop   Ready    control-plane   ...   v1.34.x
```

> **주의**: 이 시점부터 `kubectl` 명령은 로컬 클러스터에 적용됩니다.
> 프로덕션 EKS를 동시에 사용하려면:
> ```bash
> kubectl --context=arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks get pods -n platform
> ```

### Step 1: 네임스페이스 생성

```bash
kubectl apply -f infra/local-dev/00-namespaces.yaml
# platform, claude-sessions 네임스페이스 생성
```

### Step 2: PostgreSQL + Redis

```bash
kubectl apply -f infra/local-dev/01-postgresql.yaml
kubectl apply -f infra/local-dev/02-redis.yaml

# 상태 확인 (Running이 될 때까지 대기)
kubectl get pods -n platform -w
```

### Step 3: Secrets

```bash
kubectl apply -f infra/local-dev/02-secrets.yaml
# JWT 키, DB 비밀번호, 테스트 사용자 허용 등
```

### Step 4: RBAC + ServiceAccount

```bash
kubectl apply -f infra/local-dev/03-service-account.yaml
# auth-gateway가 Pod을 생성/삭제할 수 있는 권한
```

### Step 5: Auth Gateway

```bash
# 이미지 빌드 (최초 1회, 코드 변경 시마다)
docker build -t bedrock-claude/auth-gateway:local auth-gateway/

# 배포
kubectl apply -f infra/local-dev/04-auth-gateway.yaml

# 상태 확인
kubectl get pods -n platform
```

### Step 6: Nginx Ingress Controller

```bash
# 최초 1회: Ingress Controller 설치
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.0/deploy/static/provider/cloud/deploy.yaml

# Ingress Controller Ready 대기 (~30초)
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=120s

# Ingress 규칙 적용
kubectl apply -f infra/local-dev/06-ingress.yaml
```

### Step 7: OnlyOffice (선택)

```bash
kubectl apply -f infra/local-dev/07-onlyoffice.yaml
# 이미지 다운로드에 2~3분 소요
```

### Step 8: 사용자 Pod

```bash
# 이미지 빌드 (최초 1회, 코드 변경 시마다)
docker build -t bedrock-claude/claude-code-terminal:local container-image/

# AWS 자격증명을 K8s Secret으로 등록 (Bedrock 호출용)
kubectl create secret generic aws-credentials \
  --from-file=credentials=$HOME/.aws/credentials \
  --from-file=config=$HOME/.aws/config \
  -n claude-sessions \
  --dry-run=client -o yaml | kubectl apply -f -

# 테스트 사용자 Pod 생성
kubectl apply -f infra/local-dev/08-test-user-pod.yaml
```

### Step 9: 접속

```bash
open http://localhost/
# 로그인: TEST001 / test2026
```

## 접속 URL

| 서비스 | URL |
|--------|-----|
| 로그인 페이지 | http://localhost/ |
| Hub | http://localhost/hub/claude-terminal-test001/ |
| 터미널 | http://localhost/terminal/claude-terminal-test001/ |
| 파일 관리 | http://localhost/files/claude-terminal-test001/ |
| Auth Gateway API | http://localhost/api/v1/ |
| Admin Dashboard | `cd admin-dashboard && npm run dev` → http://localhost:3000 |

## 개발 워크플로

### auth-gateway 코드 수정 시

```bash
# 1. 코드 수정 (auth-gateway/app/...)
# 2. 이미지 리빌드
docker build -t bedrock-claude/auth-gateway:local auth-gateway/
# 3. Pod 재시작 (새 이미지 반영)
kubectl rollout restart deployment/auth-gateway -n platform
# 4. 브라우저에서 확인
```

### container-image (사용자 Pod) 수정 시

```bash
# 1. 코드 수정 (container-image/...)
# 2. 이미지 리빌드
docker build -t bedrock-claude/claude-code-terminal:local container-image/
# 3. 기존 Pod 삭제 + 재생성
kubectl delete pod claude-terminal-test001 -n claude-sessions
kubectl apply -f infra/local-dev/08-test-user-pod.yaml
```

### 프로덕션 배포

```bash
# 검증 완료 후 프로덕션 이미지 빌드 (amd64 필수)
docker build --platform linux/amd64 -t auth-gateway auth-gateway/
docker tag auth-gateway:latest ECR_REPO:latest
docker push ECR_REPO:latest
kubectl --context=arn:aws:eks:... rollout restart deployment/auth-gateway -n platform
```

## 전체 종료

```bash
./scripts/local-dev-down.sh
# 또는 수동:
kubectl --context=docker-desktop delete -f infra/local-dev/08-test-user-pod.yaml
kubectl --context=docker-desktop delete -f infra/local-dev/07-onlyoffice.yaml
kubectl --context=docker-desktop delete -f infra/local-dev/06-ingress.yaml
kubectl --context=docker-desktop delete -f infra/local-dev/04-auth-gateway.yaml
kubectl --context=docker-desktop delete -f infra/local-dev/02-redis.yaml
kubectl --context=docker-desktop delete -f infra/local-dev/01-postgresql.yaml
```

## 프로덕션과의 차이

| 항목 | 프로덕션 | 로컬 |
|------|---------|------|
| 이미지 | ECR (`imagePullPolicy: Always`) | 로컬 빌드 (`Never`) |
| 도메인 | claude.skons.net (HTTPS) | localhost (HTTP) |
| DB | RDS | Docker PostgreSQL |
| Redis | ElastiCache | Docker Redis |
| 파일 저장 | AWS EFS | hostPath (`~/.bedrock-local-data/`) |
| SSO | sso.skons.net | TEST001 계정 우회 |
| Claude API | Bedrock (STS 자격증명) | Bedrock (`~/.aws/` 마운트) |
| 노드 | 최대 55대 | 1대 (Docker Desktop) |

## 트러블슈팅

### Pod이 ErrImageNeverPull 상태

```bash
# 이미지가 로컬에 없음 → 빌드 필요
docker build -t bedrock-claude/auth-gateway:local auth-gateway/
kubectl rollout restart deployment/auth-gateway -n platform
```

### DB 연결 실패

```bash
# PostgreSQL Pod 상태 확인
kubectl get pods -n platform -l app=local-db
kubectl logs -n platform -l app=local-db
```

### Ingress Controller 미설치

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.0/deploy/static/provider/cloud/deploy.yaml
```

### localhost 접속 안 됨

```bash
# Ingress Controller가 Running인지 확인
kubectl get pods -n ingress-nginx
# LoadBalancer 상태 확인
kubectl get svc -n ingress-nginx
```
