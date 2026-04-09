# File Viewer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Hub 파일 탐색기에서 PDF/이미지/Office 파일을 브라우저 내 인라인으로 미리보기 (다운로드 차단)

**Architecture:** auth-gateway의 viewers API가 Pod 내 fileserver에서 파일을 프록시하여 브라우저에 스트리밍. Office 파일은 OnlyOffice DocumentServer iframe으로 편집/뷰. PDF/이미지는 브라우저 네이티브 뷰어 사용.

**Tech Stack:** FastAPI StreamingResponse, OnlyOffice DocumentServer, Tabulator.js context menu, nginx Ingress

---

## Task 1: Viewers 라우터 등록 + Pod 파일 프록시 구현

**Files:**
- Modify: `auth-gateway/app/main.py:31` (import 추가)
- Modify: `auth-gateway/app/routers/viewers.py` (전체 재구현)

**Step 1: main.py에 viewers 라우터 import + 등록**

`auth-gateway/app/main.py` line 31 import에 `viewers` 추가:
```python
from app.routers import admin, apps, auth, bots, file_share, sessions, users, sms, skills, telegram, security, scheduling, infra_policy, surveys, app_proxy, portal
from app.routers.viewers import router as viewers_router
```

line 313 부근에 등록:
```python
app.include_router(viewers_router)
```

**Step 2: viewers.py — Pod fileserver 프록시 구현**

현재 placeholder (`b"PDF content placeholder"`)를 실제 Pod fileserver 프록시로 교체.
경로: `/api/v1/viewers/file/{username}/{file_path:path}`

- username으로 Pod IP 조회 (K8s API)
- Pod의 fileserver(8080)에서 `/api/download?path={file_path}` 호출
- StreamingResponse로 브라우저에 전달
- Content-Disposition: inline (다운로드 차단)

**Step 3: 커밋**
```bash
git add auth-gateway/app/main.py auth-gateway/app/routers/viewers.py
git commit -m "feat: viewers router 등록 + Pod 파일 프록시 구현"
```

---

## Task 2: OnlyOffice Service + Ingress 생성

**Files:**
- Modify: `infra/k8s/platform/onlyoffice.yaml` (Service, Ingress 확인/적용)

**Step 1: onlyoffice.yaml에 Service + Ingress가 정의되어 있는지 확인**

현재 yaml에 Deployment + Service + Secret이 정의됨. Ingress는 없음.
OnlyOffice는 클러스터 내부에서만 접근 (auth-gateway가 iframe URL 생성).

**Step 2: kubectl apply**
```bash
kubectl apply -f infra/k8s/platform/onlyoffice.yaml
```
Service가 이미 없으면 생성됨. Deployment는 이미 Running.

**Step 3: OnlyOffice health 확인**
```bash
kubectl exec -n platform deployment/auth-gateway -- \
  python3 -c "import httpx; r=httpx.get('http://onlyoffice.claude-sessions.svc.cluster.local/healthcheck'); print(r.status_code)"
```
Expected: 200

**Step 4: 커밋** (yaml 변경 시만)
```bash
git add infra/k8s/platform/onlyoffice.yaml
git commit -m "feat: OnlyOffice Service 적용"
```

---

## Task 3: OnlyOffice 뷰어 API 엔드포인트

**Files:**
- Modify: `auth-gateway/app/routers/viewers.py` (OnlyOffice iframe URL 생성 API 추가)

**Step 1: OnlyOffice editor config 생성 엔드포인트 추가**

`GET /api/v1/viewers/office/{username}/{file_path:path}`
- JWT 토큰 생성 (OnlyOffice JWT secret 사용)
- document URL = `/api/v1/viewers/file/{username}/{file_path}` (Task 1의 프록시)
- callback URL = 빈 핸들러 (뷰 전용, 편집 저장 불필요)
- 반환: HTML 페이지 (OnlyOffice JS API iframe embed)

**Step 2: OnlyOffice JWT secret 환경변수 확인**
```bash
kubectl get secret onlyoffice-jwt-secret -n claude-sessions -o jsonpath='{.data.JWT_SECRET}' | base64 -d
```

**Step 3: 커밋**
```bash
git add auth-gateway/app/routers/viewers.py
git commit -m "feat: OnlyOffice iframe 뷰어 API"
```

---

## Task 4: Hub 파일 탐색기에 미리보기 버튼 추가

**Files:**
- Modify: `container-image/fileserver.py` (PORTAL_TEMPLATE 내 JS/HTML)

**Step 1: 컨텍스트 메뉴에 "미리보기" 항목 추가**

기존 컨텍스트 메뉴 (`fe-context-menu`)에 "미리보기" 추가:
- PDF/이미지: 새 탭에서 `/api/v1/viewers/file/{username}/{path}` 열기
- Office 파일: 새 탭에서 `/api/v1/viewers/office/{username}/{path}` 열기
- 기타 파일: 미리보기 비활성화

**Step 2: 이름 셀 싱글클릭 시 파일 타입에 따라 분기**

현재: 폴더 클릭 → 진입, 파일 클릭 → 선택만
변경: 파일 싱글클릭 시 미리보기 가능 파일이면 미리보기 실행

지원 파일 타입:
- 직접 뷰: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.svg`, `.txt`, `.md`
- OnlyOffice: `.xlsx`, `.xls`, `.csv`, `.docx`, `.doc`, `.pptx`, `.ppt`

**Step 3: Python 문법 + JS 문법 검증**
```bash
python3 -c "import ast; ast.parse(open('container-image/fileserver.py').read())"
node --check /tmp/hub-test.js  # 추출 후 검증
```

**Step 4: container-image 빌드 + ECR push**
```bash
docker buildx build --platform linux/amd64 -t claude-code-terminal --load container-image/
docker tag claude-code-terminal:latest ECR_REPO:latest
docker push ECR_REPO:latest
```
Pod 삭제 안 함 — 새 로그인 시 자동 적용.

**Step 5: 커밋**
```bash
git add container-image/fileserver.py
git commit -m "feat: Hub 파일 탐색기 미리보기 버튼 + OnlyOffice 연동"
```

---

## Task 5: auth-gateway 빌드 + 배포

**Files:** 없음 (빌드/배포만)

**Step 1: auth-gateway 빌드 + push**
```bash
docker build --platform linux/amd64 -t auth-gateway auth-gateway/
docker tag auth-gateway:latest ECR_REPO:latest
docker push ECR_REPO:latest
```

**Step 2: rollout restart**
```bash
kubectl rollout restart deployment/auth-gateway -n platform
kubectl rollout status deployment/auth-gateway -n platform --timeout=120s
```

**Step 3: 검증**
```bash
# viewers API 접근 가능 확인
kubectl exec -n platform deployment/auth-gateway -- python3 -c "
from app.routers.viewers import router
for r in router.routes:
    print(r.methods, r.path)
"
```

---

## 의존성 그래프

```
Task 2 (OnlyOffice Service) ─────────────┐
                                          ├─→ Task 5 (배포)
Task 1 (Viewers 라우터) ─→ Task 3 (Office API) ─┘
                                          │
Task 4 (Hub UI) ──────────────────────────┘
```

- Task 1, 2: 병렬 가능
- Task 3: Task 1 + 2 완료 후
- Task 4: Task 1 완료 후 (독립 — container-image)
- Task 5: Task 1 + 3 완료 후

---

## 사용자 Pod 영향

**없음.** 모든 변경은 auth-gateway + K8s 리소스 + container-image ECR push.
기존 Pod 삭제/재시작 없음. 새 로그인 시 Hub UI 자동 적용.
