# 로컬 개발 환경 구동 가이드

이 문서는 `auth-gateway`(FastAPI)와 `admin-dashboard`(Next.js)를 로컬에서 구동하는 전체 절차입니다.

---

## 사전 준비

| 도구 | 비고 |
|------|------|
| Docker Desktop | PostgreSQL + Auth Gateway 컨테이너 |
| Node.js 22 | Admin Dashboard |
| aws CLI v2 | kubeconfig 갱신 시 필요 |

---

## 1단계 — .env 파일 준비

### 기능 브랜치 worktree 작업 시

```bash
# worktree .env는 심볼릭 링크가 아닌 실제 파일이어야 함
cp auth-gateway/.env .worktrees/knowledge-intelligence/auth-gateway/.env
```

### .env 필수 항목

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/bedrock_claude
JWT_SECRET_KEY=dev-secret-key-do-not-use-in-prod

# SSO
SSO_AUTH_URL=https://authsvc.networkons.com/OAuthM.WSL/service.svc/RequestToken
SSO_AUTH_URL2=https://authsvc.networkons.com/OAuthM.WSL/service.svc/AccessProtectedResource
SSO_CLIENT_ID=SmartworksMobile
SSO_CLIENT_SECRET=OAuthSKey
SSO_AUTH_METHOD=4302010
SSO_SCOPES=NeosOAuth
SSO_TOKEN_KEY=RequestTokenResult
PW_ENCODING_SALT=1o1sqhmHcREdoi+137Rnug==

# K8s
K8S_IN_CLUSTER=false
K8S_NAMESPACE=claude-sessions
K8S_POD_IMAGE=680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest
K8S_SERVICE_ACCOUNT=claude-terminal-sa

# Bedrock
BEDROCK_REGION=ap-northeast-2
BEDROCK_SONNET_MODEL=global.anthropic.claude-sonnet-4-6
BEDROCK_HAIKU_MODEL=global.anthropic.claude-haiku-4-5-20251001-v1:0

# OnlyOffice — 32자 이상 필수 (없으면 앱 기동 실패)
ONLYOFFICE_JWT_SECRET=local-dev-only-not-for-production-use

# 로컬 전용: TEST 계정 SSO+2FA 우회
ALLOW_TEST_USERS=true
```

---

## 2단계 — Auth Gateway 구동

```bash
cd auth-gateway   # 또는 .worktrees/<branch>/auth-gateway

docker compose up --build -d
```

### 상태 확인

```bash
docker ps
# auth-gateway-api-1   Up   0.0.0.0:8000->8000/tcp
# auth-gateway-db-1    Up (healthy)   0.0.0.0:5433->5432/tcp

curl http://localhost:8000/health
# {"status":"ok","service":"auth-gateway"}
```

---

## 3단계 — DB 마이그레이션

앱 기동 시 `create_all()`로 테이블이 자동 생성됩니다.  
Alembic 이력은 별도로 동기화해야 합니다.

```bash
# 첫 실행 또는 DuplicateTable 오류 시
docker exec auth-gateway-api-1 alembic stamp head

# 이후 새 마이그레이션 적용 시
docker exec auth-gateway-api-1 alembic upgrade head
```

### 테이블 목록 확인

```bash
docker exec auth-gateway-db-1 psql -U postgres -d bedrock_claude -c "\dt"
```

---

## 4단계 — 테스트 관리자 계정 생성

로컬에서는 SSO 없이 `TESTADMIN` 계정으로 로그인합니다.

```bash
docker exec auth-gateway-api-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from app.core.database import SessionLocal
from app.models.user import User
db = SessionLocal()
if not db.query(User).filter(User.username == 'TESTADMIN').first():
    db.add(User(username='TESTADMIN', name='테스트관리자', role='admin', is_active=True, is_approved=True))
    db.commit()
    print('Created TESTADMIN')
else:
    print('Already exists')
db.close()
" 2>/dev/null
```

### 로그인 및 토큰 발급

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"TESTADMIN","password":"test2026"}' | python3 -m json.tool
```

조건: `.env`에 `ALLOW_TEST_USERS=true` + DB에 TESTADMIN 계정 존재

### API 테스트

```bash
TOKEN="<access_token 값>"

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/knowledge/graph
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/knowledge/trends
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/knowledge/workflows
```

---

## 5단계 — Admin Dashboard 구동

```bash
cd admin-dashboard

# 첫 실행 시
npm install

# 개발 서버
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

브라우저: http://localhost:3000

### Phase 2 신규 페이지

| URL | 기능 |
|-----|------|
| `/analytics/knowledge-trends` | 트렌드 그래프 + Sankey 다이어그램 + 연관 규칙 |
| `/analytics/knowledge-gap` | 워크플로우 커버리지 갭 분석 |
| `/analytics/departments` | 부서별 Knowledge 히트맵 |
| `/workflows` | 워크플로우 템플릿 캔버스 편집기 |

---

## 빠른 재시작

```bash
# Auth Gateway
cd auth-gateway && docker compose up -d
curl http://localhost:8000/health

# Admin Dashboard
cd admin-dashboard && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

---

## 트러블슈팅

### "onlyoffice_jwt_secret field required"
→ `.env`에 `ONLYOFFICE_JWT_SECRET` (32자 이상) 추가 후 재생성:
```bash
docker compose up -d api
```

### "SSO authentication failed" (TESTADMIN 로그인 실패)
→ `.env`에 `ALLOW_TEST_USERS=true` 추가 후 재생성:
```bash
docker compose up -d api   # restart는 env 미반영, up -d 필수
```

### "DuplicateTable" 마이그레이션 오류
→ 테이블이 이미 존재하므로 이력만 동기화:
```bash
docker exec auth-gateway-api-1 alembic stamp head
```

### "ConfigException: No configuration found" (K8s 오류)
→ `docker-compose.yml`에 kubeconfig 볼륨 마운트 확인:
```yaml
volumes:
  - ./app:/app/app
  - ~/.kube/config:/root/.kube/config:ro
```

### .env 변경이 컨테이너에 반영되지 않음
`docker compose restart`는 `env_file` 변경을 반영하지 않습니다:
```bash
docker compose up -d api   # 컨테이너 재생성
```
