# 개발 → 검증 → 프로덕션 배포 워크플로우

이 문서는 로컬 개발환경 구성부터 프로덕션 배포까지 전체 흐름을 정리합니다.

---

## 전체 흐름

```
[코드 작성] → [로컬 환경 구동] → [로컬 검증] → [ECR Push / Amplify 배포] → [EKS 반영] → [프로덕션 확인]
```

| 단계 | 스크립트 / 명령 | 소요 시간 |
|------|----------------|----------|
| 로컬 환경 구동 | `./scripts/local-dev-up.sh` | ~2분 (최초) / ~30초 (재기동) |
| SSM 터널 (선택) | `./scripts/ssm-tunnels.sh start` | ~10초 |
| 로컬 테스트 | `http://localhost/` | — |
| 이미지 빌드 & Push | `docker buildx build` + `docker push` | ~3-5분 |
| EKS 반영 | `kubectl rollout restart` | ~60초 |
| 프로덕션 확인 | `https://claude.skons.net/` | — |

---

## 1. 로컬 개발환경

### 1-1. 사전 요구사항

| 도구 | 확인 |
|------|------|
| Docker Desktop (K8s 활성화) | `kubectl --context=docker-desktop get nodes` |
| AWS CLI v2 + SSO 로그인 | `aws sts get-caller-identity` |
| session-manager-plugin | `brew install --cask session-manager-plugin` |

```bash
# AWS SSO 로그인 (만료 시 재실행)
aws sso login
```

### 1-2. 전체 환경 구동

```bash
./scripts/local-dev-up.sh
```

**내부 동작 순서:**
1. Docker Desktop K8s 연결 확인
2. auth-gateway + claude-code-terminal 이미지 빌드
3. kind 노드에 이미지 적재 (`docker save | ctr import`)
4. namespace / PostgreSQL / Redis / Secrets / RBAC 적용
5. auth-gateway 배포 + 대기
6. DB seed (TEST001 사용자)
7. Nginx Ingress Controller 설치 (최초 1회)
8. OnlyOffice + 테스트 사용자 Pod 구동

**접속 정보:**
| 항목 | URL |
|------|-----|
| 로그인 | `http://localhost/` |
| 계정 | TEST001 / test2026 |
| 터미널 | `http://localhost/terminal/claude-terminal-test001/` |
| 파일관리 | `http://localhost/files/claude-terminal-test001/` |
| Admin Dashboard | `http://localhost:3000` (별도 실행 필요 — 1-3 참고) |

### 1-3. Admin Dashboard 로컬 실행 (선택)

Admin Dashboard(`admin-dashboard/`)는 `local-dev-up.sh`에 포함되지 않습니다.  
별도 dev server로 실행해야 합니다.

**최초 설정 (1회):**
```bash
cd admin-dashboard
cp .env.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000 로컬 설정
npm install
```

**실행:**
```bash
# 터미널 1: auth-gateway port-forward (8000 포트 — admin dashboard API 통신용)
kubectl --context=docker-desktop port-forward -n platform svc/auth-gateway 8000:8000

# 터미널 2: admin dashboard dev server
cd admin-dashboard
npm run dev
```

**접속:** `http://localhost:3000`

> **주의:** `.env.local`의 `NEXT_PUBLIC_API_URL`이 `http://localhost:8000`이어야 합니다.  
> 프로덕션 URL(`https://claude.skons.net`)로 되어 있으면 로컬 auth-gateway에 연결되지 않습니다.

### 1-4. SSM 터널 (프로덕션 DB 연결 — 선택)

로컬에서 프로덕션 DB(`safety-prod`, `aiagentdb`)를 읽어야 할 때 사용합니다.  
`local-dev-up.sh` **이후** 실행합니다.

```bash
# 시작 (터널 + K8s secret 자동 패치)
./scripts/ssm-tunnels.sh start

# auth-gateway 재시작으로 새 DB URL 적용
kubectl rollout restart deployment/auth-gateway -n platform --context docker-desktop

# 상태 확인
./scripts/ssm-tunnels.sh status

# 종료
./scripts/ssm-tunnels.sh stop
```

터널 포트:
| DB | 로컬 포트 | 용도 |
|----|---------|------|
| safety-prod-db-readonly | `localhost:5434` | `WORKSHOP_DATABASE_URL` |
| aiagentdb | `localhost:5435` | `TANGO_DATABASE_URL`, `DOCULOG_DATABASE_URL` |

> **재기동 후 패치만 다시 적용할 때:**
> ```bash
> ./scripts/ssm-tunnels.sh patch
> kubectl rollout restart deployment/auth-gateway -n platform --context docker-desktop
> ```

### 1-5. 환경 종료

```bash
./scripts/local-dev-down.sh
./scripts/ssm-tunnels.sh stop  # 터널 사용 중이면
```

---

## 2. 개발 & 로컬 검증

### 코드 변경 후 반영

| 변경 대상 | 반영 방법 |
|-----------|----------|
| `auth-gateway/` | `docker build` → `docker save \| ctr import` → `kubectl rollout restart` |
| `container-image/` | `docker build` → `docker save \| ctr import` → Pod 재생성 |
| `infra/local-dev/*.yaml` | `kubectl apply -f <파일>` |

**auth-gateway 빠른 재빌드:**
```bash
docker build -q -t bedrock-claude/auth-gateway:local auth-gateway/
docker save bedrock-claude/auth-gateway:local \
  | docker exec -i desktop-control-plane ctr -n k8s.io images import -
kubectl rollout restart deployment/auth-gateway -n platform --context docker-desktop
kubectl rollout status deployment/auth-gateway -n platform --context docker-desktop
```

**테스트 Pod 재생성:**
```bash
kubectl delete pod claude-terminal-test001 -n claude-sessions --context docker-desktop
kubectl apply -f infra/local-dev/08-test-user-pod.yaml --context docker-desktop
```

### 검증 체크리스트

- [ ] `http://localhost/` 로그인 정상
- [ ] 터미널 접속 후 Claude Code 실행
- [ ] `curl http://localhost/health` → `{"status":"ok"}`
- [ ] SSM 터널 사용 시: Pod 내부에서 `psql "$WORKSHOP_DATABASE_URL" -c "select 1"`

---

## 3. 프로덕션 배포

> **공통 전제:**
> - 반드시 `--platform linux/amd64` 빌드 (Mac ARM → EKS x86_64)
> - `:latest` 태그 사용 (digest 고정 금지)
> - 사용자 Pod 삭제 금지 (재로그인 시 자동 교체)

**ECR 공통 정보:**
```
Account: 680877507363
Region : ap-northeast-2
ECR URL: 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com
Cluster: arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks
```

**ECR 로그인 (세션 당 1회):**
```bash
aws ecr get-login-password --region ap-northeast-2 \
  | docker login --username AWS --password-stdin \
    680877507363.dkr.ecr.ap-northeast-2.amazonaws.com
```

---

### 3-1. auth-gateway 배포

```bash
# 1. 빌드
docker buildx build --platform linux/amd64 \
  -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest \
  auth-gateway/ --push

# 2. EKS 반영
PROD="arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks"
kubectl rollout restart deployment/auth-gateway -n platform --context "$PROD"
kubectl rollout status deployment/auth-gateway -n platform --context "$PROD" --timeout=120s
```

> **주의:** Deployment spec에 `@sha256:` digest가 고정되어 있으면 rollout restart만으로 반영 안 됨.
> ```bash
> # 확인
> kubectl get deploy auth-gateway -n platform --context "$PROD" \
>   -o jsonpath='{.spec.template.spec.containers[0].image}'
> # 수정 (digest 박혀 있을 때)
> kubectl set image deployment/auth-gateway -n platform --context "$PROD" \
>   auth-gateway=680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest
> ```

---

### 3-2. usage-worker 배포

```bash
# 1. 빌드
docker buildx build --platform linux/amd64 \
  -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/usage-worker:latest \
  usage-worker/ --push

# 2. EKS 반영
PROD="arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks"
kubectl rollout restart deployment/usage-worker -n platform --context "$PROD"
kubectl rollout status deployment/usage-worker -n platform --context "$PROD" --timeout=60s
```

---

### 3-3. container-image (사용자 터미널 Pod) 배포

> 배포 후 **기존 Pod은 삭제하지 않습니다.** 사용자가 재로그인하면 새 이미지로 Pod이 자동 생성됩니다.

```bash
# 1. 빌드
docker buildx build --platform linux/amd64 \
  -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest \
  container-image/ --push

# 2. 확인 (기존 Pod 교체는 재로그인 시 자동)
echo "✅ 새 이미지 push 완료. 사용자 재로그인 시 자동 적용."
```

새 버전을 즉시 강제 적용해야 할 때 (긴급 패치 등):
```bash
# 특정 사용자 Pod만 삭제 (해당 사용자 재접속 시 새 이미지 생성)
PROD="arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks"
kubectl delete pod -l user-id=<USERNAME> -n claude-sessions --context "$PROD"
```

---

### 3-4. admin-dashboard 배포

**플랫폼**: AWS Amplify (S3+CloudFront 아님!)  
**App ID**: `d3n19scky0a5sa` | **Branch**: `main`  
**Domain**: `claude-admin.skons.net`

```bash
# 1. 빌드
cd admin-dashboard
npm run build

# 2. 배포 준비
cd out
zip -r /tmp/amplify-deploy.zip . -x ".DS_Store"

# 3. Amplify 배포 생성
DEPLOY=$(aws amplify create-deployment \
  --app-id d3n19scky0a5sa \
  --branch-name main \
  --output json)
JOB_ID=$(echo "$DEPLOY" | python3 -c "import sys,json; print(json.load(sys.stdin)['jobId'])")
ZIP_URL=$(echo "$DEPLOY" | python3 -c "import sys,json; print(json.load(sys.stdin)['zipUploadUrl'])")

# 4. 업로드
python3 -c "
import urllib.request
with open('/tmp/amplify-deploy.zip','rb') as f: data=f.read()
req=urllib.request.Request('$ZIP_URL',data=data,method='PUT')
req.add_header('Content-Type','application/zip')
urllib.request.urlopen(req)
print('Uploaded')
"

# 5. 배포 시작
aws amplify start-deployment \
  --app-id d3n19scky0a5sa \
  --branch-name main \
  --job-id "$JOB_ID"

# 6. 배포 완료 확인
aws amplify get-job \
  --app-id d3n19scky0a5sa \
  --branch-name main \
  --job-id "$JOB_ID" \
  --query "job.summary.status"
# "SUCCEED" 나올 때까지 대기 (~2-3분)
```

---

## 4. 배포 후 확인

```bash
PROD="arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks"

# Pod 상태
kubectl get pods -n platform --context "$PROD"
kubectl get pods -n claude-sessions --context "$PROD" | head -10

# auth-gateway 로그 (최근 20줄)
kubectl logs -l app=auth-gateway -n platform --context "$PROD" --tail=20

# 헬스체크
curl -s https://claude.skons.net/health | python3 -m json.tool

# admin-dashboard
# https://claude-admin.skons.net 접속 확인
```

---

## 5. 롤백

### auth-gateway / usage-worker 롤백

```bash
PROD="arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks"

# 이전 Revision으로 롤백
kubectl rollout undo deployment/auth-gateway -n platform --context "$PROD"
kubectl rollout status deployment/auth-gateway -n platform --context "$PROD"
```

### container-image 롤백

이전 이미지를 ECR에 재push하거나 tag를 되돌립니다:
```bash
# 이전 이미지로 재빌드 & push (git checkout <이전 커밋> 후)
docker buildx build --platform linux/amd64 \
  -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest \
  container-image/ --push
# 기존 Pod 영향 없음 — 신규 로그인부터 이전 이미지 사용
```

### admin-dashboard 롤백

```bash
# 이전 job-id로 재배포
aws amplify list-jobs \
  --app-id d3n19scky0a5sa \
  --branch-name main \
  --query "jobSummaries[*].[jobId,status,commitMessage]" \
  --output table
# 원하는 jobId로 재배포:
# aws amplify start-deployment --app-id d3n19scky0a5sa --branch-name main --job-id <이전-jobId>
```

---

## 6. 핵심 주의사항

| 항목 | 규칙 |
|------|------|
| **빌드 플랫폼** | 반드시 `--platform linux/amd64` (Mac ARM 빌드 금지) |
| **이미지 태그** | `:latest` 고정, digest(`@sha256:`) 박으면 rollout restart 무효 |
| **사용자 Pod 삭제** | 운영 중 삭제 금지. 재로그인 시 자동 교체 |
| **auth-gateway 복제본** | 2개 유지, hard anti-affinity (system-node-large 노드 2대) |
| **SSM 터널 stop** | `stop` 시 K8s secret URL이 초기화됨 → 이후 `patch` 재실행 필요 |
| **kind 이미지 적재** | 로컬 `docker build` 후 반드시 `docker save \| ctr import`로 kind 노드에 적재 |
| **AWS 태그** | 신규 리소스: `Owner=N1102359`, `Env=<환경>`, `Service=<서비스명>` 필수 |

---

## 7. 빠른 참조

### 로컬 전체 재기동

```bash
./scripts/local-dev-down.sh
./scripts/local-dev-up.sh
./scripts/ssm-tunnels.sh start           # 프로덕션 DB 필요 시
kubectl rollout restart deployment/auth-gateway -n platform --context docker-desktop

# Admin Dashboard 로컬 확인이 필요한 경우 (별도 터미널):
kubectl --context=docker-desktop port-forward -n platform svc/auth-gateway 8000:8000 &
cd admin-dashboard && npm run dev        # → http://localhost:3000
```

### 프로덕션 전체 배포 순서 (코드 변경 후)

```bash
# 1. auth-gateway
docker buildx build --platform linux/amd64 \
  -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest \
  auth-gateway/ --push
kubectl rollout restart deployment/auth-gateway -n platform \
  --context arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks

# 2. container-image (변경 시)
docker buildx build --platform linux/amd64 \
  -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest \
  container-image/ --push

# 3. usage-worker (변경 시)
docker buildx build --platform linux/amd64 \
  -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/usage-worker:latest \
  usage-worker/ --push
kubectl rollout restart deployment/usage-worker -n platform \
  --context arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks

# 4. admin-dashboard (변경 시)
cd admin-dashboard && npm run build
# → 위 3-4 섹션의 Amplify 배포 명령 실행
```

### 환경 정보

| 항목 | 값 |
|------|-----|
| EKS 컨텍스트 | `arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks` |
| ECR 베이스 URL | `680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/` |
| Amplify App ID | `d3n19scky0a5sa` |
| 프로덕션 URL | `https://claude.skons.net/` |
| Admin URL | `https://claude-admin.skons.net/` |
| 로컬 URL | `http://localhost/` |
