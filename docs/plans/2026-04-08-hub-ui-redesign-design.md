# Hub UI 재설계 + 웹앱 버전 관리 + 스킬 공유 설계

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Hub 페이지 UI 전면 재설계 — 웹앱 멀티 배포 관리, 포트 자동 할당, 로컬 git 기반 버전 관리, 통합 공유 관리, 스킬 공유 체계

**Architecture:** fileserver.py Hub HTML 재설계 + 신규 API 엔드포인트 + deploy.sh 보강 + auth-gateway 포트 라우팅 확장

**Tech Stack:** Python (fileserver.py), Vanilla JS (Hub HTML), FastAPI (auth-gateway), K8s Ingress

---

## 1. 설계 결정 요약

| 항목 | 결정 |
|------|------|
| 헤더 | "Claude Code Terminal" → **"Otto AI 터미널"** |
| 웹앱 카드 | 유지, 앱 목록으로 스크롤 이동 + 실행 수 표시 + "모두 실행중지" |
| 미배포 앱 정의 | A) 실행 중 dev 서버 + B) workspace 프로젝트 + C) 이전 배포 삭제됨 |
| 앱 감지 | package.json, requirements.txt, Dockerfile 다중 감지 |
| 포트 할당 | 3000~3100 자동 할당 (가장 낮은 빈 포트) |
| URL 패턴 | `/app/{pod_name}/{app-name}/` → `localhost:{port}` (이름 기반) |
| 앱 레지스트리 | `/workspace/.webapp-registry.json` (자동 등록 + 이름 수정 가능) |
| 버전 관리 | 로컬 git (deploy.sh 기존 기능 보강). GitLab/Gitea 도입 안 함 |
| 버전 UI | "이전 버전 보기" 모달, 한국어 날짜, 별명 편집 |
| branch | 노출 안 함. 선형 히스토리(v1→v2→v3)만 |
| 공유 관리 | 앱+데이터셋 통합 화면, 체크박스 일괄 해제 |
| 스크롤바 | 모든 목록 5개 이상 시 max-height + overflow-y |
| 보안 정책 | CLAUDE.md 반영 완료 (자체 인증/API 전용/데이터 허브 금지) |
| 탭 구조 | 3탭: "앱/데이터 관리" \| "스킬 관리" \| "명령어 가이드" |
| 스킬 공유 | 수동 설치 방식 (Hub에서 "설치" 버튼), slash command + 워크플로 스킬 |
| 스킬 스토어 | 인기 랭킹 (설치 횟수) + 개인 추천 (향후 구현) |

---

## 2. 헤더 변경

```
Before: "Claude Code Terminal"
After:  "Otto AI 터미널"
```

하위 텍스트 유지: `{사용자명} ({사번}) · {pod_name}`

---

## 3. 탭 구조 변경

### 기존 (2탭)
```
[앱/데이터 관리]  [명령어 가이드]
```

### 변경 (3탭)
```
[앱/데이터 관리]  [스킬 관리]  [명령어 가이드]
```

---

## 4. 웹앱 카드 재설계

### 기존
```html
<a class="card" href="/app/{pod_name}/" target="_blank">
  <div class="icon">&#127760;</div>
  <h2>웹앱</h2>
  <p>터미널에서 만든 대시보드<br>웹앱 접속 (포트 3000)</p>
  <span class="badge badge-green">새 탭에서 열기</span>
</a>
```

### 변경
```html
<div class="card" onclick="scrollToApps()">
  <div class="icon">&#127760;</div>
  <h2>웹앱</h2>
  <p id="webappCardDesc">실행 중인 앱 없음</p>
  <span class="badge" id="webappCardBadge">앱 관리로 이동</span>
  <!-- 실행 중 앱 1개 이상일 때 표시 -->
  <button class="btn-stop-all" id="stopAllBtn" style="display:none"
          onclick="event.stopPropagation(); stopAllApps()">모두 실행중지</button>
  <!-- 3개 이상일 때 경고 -->
  <div class="resource-warning" id="resourceWarning" style="display:none">
    ⚠️ 실행 중인 앱이 많으면 AI 에이전트 성능이 저하됩니다.
    지금은 개발목적이니, 최소한의 앱만 구동하기를 권장드립니다.
  </div>
</div>
```

### 상태별 표시
- 0개 실행: "실행 중인 앱 없음" + badge-green
- 1~2개: "2개 실행 중" + badge-green
- 3개+: "3개 실행 중" + badge-yellow + 경고 배너 표시

---

## 5. "앱/데이터 관리" 탭 재구성

### 섹션 구성 (5개)

| # | 섹션 | 변경 |
|---|------|------|
| 1 | **내 웹앱** | 재설계 — 배포됨/실행중/미실행/삭제됨 통합 목록 |
| 2 | 공유 받은 앱 | 변경 없음 |
| 3 | 내 공유 데이터 | 변경 없음 |
| 4 | 공유 받은 데이터 | 변경 없음 |
| 5 | **내 공유 관리** | 신규 — 앱+데이터 통합 공유 일괄 관리 |

### 4.1 "내 웹앱" 섹션

#### 앱 상태 4종

| 상태 | 소스 | 배지 색상 | 액션 버튼 |
|------|------|----------|----------|
| 배포됨 | DB `status=running` | 🟢 초록 | [열기] [이전 버전 보기] [접근관리] [...삭제] |
| 실행중 | Pod 포트 스캔 3000~3100 | 🔵 파랑 | [열기] [실행중지] [배포] [삭제] |
| 미실행 | workspace 프로젝트 감지 | ⚪ 회색 | [실행하기] [배포] [삭제] |
| 삭제됨 | DB `status=deleted` | 🔴 흐리게 | [재배포] |

#### 앱 목록 항목 구조

```
[alarm-viewer ✏️]  Python · :3001 · 실행중    [열기] [실행중지] [삭제]
[my-dashboard ✏️]  v3 · 4월 8일 배포 · 배포됨  [...] 
[todo-app ✏️]      React (package.json) · 미실행 [실행하기] [배포] [삭제]
[old-report ✏️]    v1 · 삭제됨                  [재배포]
```

- ✏️: 앱 이름 인라인 수정 (webapp-registry.json 업데이트)
- [...]: 더보기 메뉴 (이전 버전 보기, 접근관리, 삭제)

### 4.2 "내 공유 관리" 섹션 (신규)

```
내 공유 관리 (12)
┌─────────────────────────────────────────────────────┐
│ ☐ 전체 선택                      [선택 항목 공유 해제] │
│─────────────────────────────────────────────────────│
│ ☐ [앱] my-dashboard → 전사 공개           [공유관리] │
│ ☐ [앱] my-dashboard → 경남담당 (지역)      [공유관리] │
│ ☐ [앱] my-dashboard → N1103906 (개인)     [공유관리] │
│ ☐ [데이터] erp-2026q1 → 품질혁신팀 (팀)    [공유관리] │
│ ☐ [데이터] erp-2026q1 → N1105000 (개인)   [공유관리] │
│─────────────────────────────────────────────────────│
│              스크롤 (5개 이상 시)                      │
└─────────────────────────────────────────────────────┘
```

- 체크박스 선택 → "선택 항목 공유 해제" 일괄 revoke
- "전체 선택" 체크박스
- 각 행 "공유관리" → 기존 ACL/공유 모달 열기
- `[앱]` `[데이터]` 태그로 유형 구분

---

## 6. 앱 레지스트리 (webapp-registry.json)

### 파일 위치
```
/workspace/.webapp-registry.json
```

### 스키마
```json
{
  "my-dashboard": {
    "port": 3000,
    "path": "/workspace/my-dashboard",
    "type": "node",
    "auto_detected": true,
    "created_at": "2026-04-08T14:30:00Z"
  },
  "alarm-viewer": {
    "port": 3001,
    "path": "/workspace/flask-api",
    "type": "python",
    "auto_detected": true,
    "created_at": "2026-04-08T15:00:00Z"
  }
}
```

### 등록 흐름
1. **자동 감지**: `/workspace/` 1단계 하위 스캔 → package.json / requirements.txt / Dockerfile 있으면 자동 등록 (디렉토리명 = 앱 이름)
2. **이름 수정**: Hub에서 ✏️ 클릭 → 인라인 수정 / 터미널에서 `webapp rename old new`
3. **포트 할당**: "실행하기" 시 3000~3100 중 비어있는 가장 낮은 포트 자동 할당
4. **포트 변경**: dev 서버 재시작 시 이전 포트 우선 재사용, 충돌 시 다음 빈 포트

---

## 7. 포트 감지 & 앱 실행/중지 API

### fileserver.py 신규 API 엔드포인트

| 엔드포인트 | 메서드 | 기능 |
|-----------|--------|------|
| `/api/apps/status` | GET | 레지스트리 + 포트 3000~3100 listening 프로세스 통합 조회 |
| `/api/apps/projects` | GET | workspace 내 프로젝트 디렉토리 스캔 |
| `/api/apps/start` | POST | 프로젝트 실행 (자동 포트 할당 + 레지스트리 등록) |
| `/api/apps/stop` | POST | 특정 앱 프로세스 kill |
| `/api/apps/stop-all` | POST | 3000~3100 전체 프로세스 kill |
| `/api/apps/rename` | POST | 앱 이름 변경 (레지스트리 업데이트) |
| `/api/apps/delete-project` | POST | workspace 프로젝트 디렉토리 삭제 |
| `/api/apps/versions/{app}` | GET | git tag 기반 버전 목록 조회 |
| `/api/apps/versions/{app}/label` | PUT | 버전 별명 수정 |

### 포트 스캔 방식
```python
# Pod 내부에서 listening 포트 스캔
import subprocess
result = subprocess.run(['ss', '-tlnp'], capture_output=True, text=True)
# 또는
result = subprocess.run(['lsof', '-iTCP:3000-3100', '-sTCP:LISTEN', '-P'],
                        capture_output=True, text=True)
```

반환 형태:
```json
[
  {"port": 3000, "pid": 1234, "command": "node", "cwd": "/workspace/my-app"},
  {"port": 3001, "pid": 5678, "command": "python3", "cwd": "/workspace/flask-api"}
]
```

### 프로젝트 감지 방식
```python
# /workspace/ 1단계 하위만 스캔 (깊은 탐색 방지)
for d in os.listdir("/workspace"):
    path = f"/workspace/{d}"
    if not os.path.isdir(path) or d.startswith('.'):
        continue
    if os.path.isfile(f"{path}/package.json"):
        project_type = "node"
    elif os.path.isfile(f"{path}/requirements.txt"):
        project_type = "python"
    elif os.path.isfile(f"{path}/Dockerfile"):
        project_type = "docker"
    else:
        continue  # 인식 불가 디렉토리는 무시
```

### 앱 실행 명령 매핑

| type | 실행 명령 | 환경변수 |
|------|----------|---------|
| node | `npm run dev` 또는 `npm start` | `PORT={assigned}` |
| python | `python3 -m uvicorn app:app --host 0.0.0.0 --port {assigned}` | — |
| docker | 미지원 (향후 확장) | — |

---

## 8. URL 라우팅 확장

### 현재
```
/app/{pod_name}/  →  localhost:3000  (고정)
```

### 변경
```
/app/{pod_name}/{app-name}/  →  localhost:{registry에서 조회한 포트}
```

### fileserver.py 리버스 프록시 확장

```python
# do_GET에서 /app/{pod_name}/{app-name}/ 패턴 매칭
# webapp-registry.json에서 app-name → port 조회
# localhost:{port}로 프록시
```

### Ingress 변경

사용자 Pod Ingress에 `/app/{pod_name}/{app-name}/` 와일드카드 패턴 추가:
```yaml
- path: /app/{pod_name}(/|$)(.*)
  pathType: ImplementationSpecific
  backend:
    service:
      name: {pod_name}
      port:
        number: 8080  # fileserver가 내부 라우팅 처리
```

fileserver.py가 8080 포트에서 Hub + 리버스 프록시를 모두 담당.

---

## 9. 버전 관리 (로컬 git 기반)

### 원칙
- **git은 백엔드 구현 수단**. 사용자에게 "git"이라는 단어를 노출하지 않음
- 선형 버전 히스토리만 (v1 → v2 → v3). branch 없음
- deploy 시점에 자동 commit + tag. 사용자 개입 불필요

### 사용자 용어 매핑

| git 내부 | 사용자 용어 |
|---------|-----------|
| commit + tag | 저장 시점 (4월 8일 오후 3:15) |
| tag list | 이전 버전 보기 |
| checkout | 이 버전으로 돌아가기 |
| HEAD | 현재 버전 |
| commit message | 별명 (선택적 입력, AI 자동 생성 가능) |

### deploy.sh 보강

```bash
# 기존 git init/commit/tag는 유지

# 추가 기능:
deploy my-app --versions       # 태그 목록 (배포 이력)
deploy my-app --history        # git log (커밋 이력)
deploy my-app --restore v-XXX  # 특정 버전으로 복원 후 재배포

# 안전장치:
# - index.lock 감지 (Claude Code 동시 작업 방지)
# - MAX_SNAPSHOTS=10 (오래된 스냅샷 자동 정리)
# - .gitignore 자동 생성 (node_modules, __pycache__, .env)
```

### 버전 기록 모달 (Hub UI)

```
┌──────────────────────────────────────────────┐
│  my-dashboard — 버전 기록               [X]   │
│                                               │
│  현재 >>>                                     │
│  ┌──────────────────────────────────────────┐ │
│  │ 4월 8일 오후 3:15              [현재 버전] │ │
│  │ "분기별 실적 차트 추가" ✏️                 │ │
│  └──────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────┐ │
│  │ 4월 5일 오전 10:22                        │ │
│  │ "팀별 필터 기능 추가" ✏️     [돌아가기]    │ │
│  └──────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────┐ │
│  │ 4월 1일 오후 2:30                         │ │
│  │ "최초 배포" ✏️               [돌아가기]    │ │
│  └──────────────────────────────────────────┘ │
│                                               │
│  총 3개 버전 · 최초 배포: 4월 1일              │
└──────────────────────────────────────────────┘
```

### "돌아가기" 확인 대화상자

```
┌──────────────────────────────────────────────┐
│  이전 버전으로 돌아가기                        │
│                                               │
│  "my-dashboard"를 아래 버전으로 되돌립니다:     │
│                                               │
│    4월 5일 오전 10:22                         │
│    "팀별 필터 기능 추가"                       │
│                                               │
│  현재 버전(4월 8일)은 버전 기록에 보관되며,     │
│  언제든 다시 돌아올 수 있습니다.                │
│                                               │
│       [취소]          [되돌리기]               │
└──────────────────────────────────────────────┘
```

### 버전 별명 저장

레지스트리 확장 또는 별도 파일:
```
/workspace/.webapp-versions.json
{
  "my-dashboard": {
    "v-20260408-1515": "분기별 실적 차트 추가",
    "v-20260405-1022": "팀별 필터 기능 추가",
    "v-20260401-1430": "최초 배포"
  }
}
```

---

## 10. 안전장치

### 삭제 확인 강화
- 배포 앱 삭제: 앱 이름 재입력 확인 ("삭제하려면 앱 이름을 입력하세요")
- 프로젝트 삭제: 확인 모달 ("이 프로젝트를 삭제합니다. 되돌릴 수 없습니다.")
- 실행 중 앱 삭제: 프로세스 먼저 중지 후 디렉토리 삭제

### 버전 돌아가기
- 현재 버전 자동 보존 (새 스냅샷으로 저장 후 symlink 전환)
- 확인 대화상자에 "현재 버전은 보관됩니다" 명시

### 자동 정리
- 최근 10개 버전 유지 (MAX_SNAPSHOTS=10)
- 오래된 버전 자동 삭제 (사용자에게 알리지 않음)

---

## 11. 스크롤바

### 모든 목록에 동일 적용

| 섹션 | 임계값 | max-height |
|------|--------|------------|
| 내 웹앱 | 5개 이상 | 400px |
| 공유 받은 앱 | 5개 이상 | 300px |
| 내 공유 데이터 | 5개 이상 | 300px |
| 공유 받은 데이터 | 5개 이상 | 300px |
| 내 공유 관리 | 5개 이상 | 400px |

### 스타일
```css
.app-list { max-height: 400px; overflow-y: auto; }
.app-list::-webkit-scrollbar { width: 6px; }
.app-list::-webkit-scrollbar-track { background: #30363d; border-radius: 3px; }
.app-list::-webkit-scrollbar-thumb { background: #484f58; border-radius: 3px; }
.app-list::-webkit-scrollbar-thumb:hover { background: #6e7681; }
```

---

## 12. 변경 파일 목록

| 파일 | 변경 내용 | 규모 |
|------|----------|------|
| `container-image/fileserver.py` | Hub HTML 전면 재설계 + 신규 API 9개 + 리버스 프록시 확장 + 레지스트리 관리 | 대 |
| `container-image/config/CLAUDE.md` | ✅ 완료 — 웹앱 보안 정책 추가 | 완료 |
| `container-image/scripts/deploy.sh` | --versions, --history, --restore 서브커맨드 + index.lock 감지 + .gitignore + MAX_SNAPSHOTS | 중 |
| `auth-gateway/app/routers/apps.py` | 통합 공유 관리 API (`/my-shares`, bulk revoke) | 중 |
| `auth-gateway/app/services/app_deploy_service.py` | Ingress 패턴 변경 (앱 이름 기반 라우팅) | 소 |
| `auth-gateway/app/services/k8s_service.py` | Pod Ingress에 `/app/{pod}/{app-name}/` 패턴 추가 | 소 |
| `auth-gateway/app/routers/skills.py` | (Phase 3) 스킬 스토어 API 신규 | 중 |
| `auth-gateway/app/models/skill.py` | (Phase 3) SharedSkill, SkillInstall 모델 | 소 |

---

## 13. 보안 정책 (CLAUDE.md 반영 완료)

### 금지 1: 자체 사용자 인증/관리 구현 금지
- 로그인 폼, 회원가입, 세션 관리, JWT 발급 등 자체 인증 시스템 금지
- 플랫폼(SSO + 2FA)이 Ingress 레벨에서 처리

### 금지 2: API 전용 서비스 금지
- 반드시 HTML UI(프론트엔드) 포함 필수
- JSON만 반환하는 엔드포인트만 구성 불가

### 금지 3: 데이터 우회 허브 금지
- Office 제품, Tableau, Power BI 등 외부 도구 연동 API 금지
- CSV 다운로드 API, OData, RSS 피드 등 금지

---

## 14. 스킬 공유 체계 (Phase 3)

### 개요

사용자가 자신의 Claude Code 스킬(slash command, 워크플로 자동화)을 다른 사용자에게 공유하는 "스킬 스토어" 체계.

### 스킬의 두 가지 유형

| 유형 | 설명 | 예시 |
|------|------|------|
| **Slash Command 스킬** | `.claude/skills/` 디렉토리의 SKILL.md 파일 | `/db-report` → TBM 현황 자동 조회 + 차트 |
| **워크플로 스킬** | 여러 단계를 자동화하는 프롬프트 패키지 | "매주 월요일 안전점검 보고서 생성" 워크플로 |

### 탭 위치

```
[앱/데이터 관리]  [스킬 관리]  [명령어 가이드]
```

### "스킬 관리" 탭 내부 구조 (3섹션)

#### 섹션 1: 내 스킬

내가 만든 스킬 목록. 공유/미공유 상태 표시.

```
내 스킬 (3)
┌──────────────────────────────────────────────────┐
│ [/db-report]  TBM 현황 조회 + 차트    [공유하기]   │
│   slash command · 미공유                          │
│──────────────────────────────────────────────────│
│ [/weekly-safety]  주간 안전 보고서 생성   공유 중   │
│   워크플로 · 12명 설치 · ⭐ 4.2         [관리]     │
│──────────────────────────────────────────────────│
│ [/alarm-check]  실시간 고장 현황         공유 중   │
│   slash command · 8명 설치 · ⭐ 4.5     [관리]     │
└──────────────────────────────────────────────────┘
```

액션:
- **공유하기**: 스킬을 스토어에 등록 (이름, 설명, 카테고리 입력)
- **관리**: 설명 수정, 공유 해제, 설치 통계 확인

#### 섹션 2: 스킬 스토어

다른 사용자가 공유한 스킬 목록. 인기순 정렬.

```
스킬 스토어 (15)                    [인기순 ▼] [검색...]
┌──────────────────────────────────────────────────┐
│ 🏆 /alarm-check     실시간 고장 현황               │
│   N1102359 · 45명 설치 · ⭐ 4.8        [설치]      │
│──────────────────────────────────────────────────│
│ 🥈 /weekly-safety   주간 안전 보고서 생성           │
│   N1102359 · 32명 설치 · ⭐ 4.5        [설치]      │
│──────────────────────────────────────────────────│
│    /erp-dashboard   ERP 데이터 대시보드             │
│   N1105000 · 12명 설치 · ⭐ 4.0        [설치]      │
│──────────────────────────────────────────────────│
│    /tango-report    TANGO 알람 일일 보고            │
│   N1103906 · 8명 설치 · ⭐ 3.8         [설치]      │
└──────────────────────────────────────────────────┘
```

기능:
- **인기순 정렬**: 설치 횟수 기준 (기본)
- **검색**: 스킬 이름/설명 키워드 검색
- **설치 버튼**: 클릭 → 내 Pod의 `.claude/skills/`에 복사
- **카테고리 필터** (향후): 데이터조회, 보고서, 차트, 업무자동화 등

#### 섹션 3: 설치된 스킬

스토어에서 설치한 스킬 목록.

```
설치된 스킬 (2)
┌──────────────────────────────────────────────────┐
│ [/erp-dashboard]  ERP 데이터 대시보드    [제거]    │
│   N1105000 제작 · 4월 5일 설치                    │
│──────────────────────────────────────────────────│
│ [/tango-report]   TANGO 알람 일일 보고   [제거]    │
│   N1103906 제작 · 4월 3일 설치                    │
└──────────────────────────────────────────────────┘
```

### 데이터 모델

#### shared_skills 테이블 (auth-gateway DB)

```sql
CREATE TABLE shared_skills (
    id SERIAL PRIMARY KEY,
    owner_username VARCHAR(20) NOT NULL,    -- 제작자 사번
    skill_name VARCHAR(100) NOT NULL,       -- slash command 이름 (예: /db-report)
    display_name VARCHAR(200),              -- 표시 이름
    description TEXT,                       -- 설명
    skill_type VARCHAR(20) DEFAULT 'slash_command',  -- slash_command | workflow
    category VARCHAR(50),                   -- 카테고리 (향후)
    install_count INTEGER DEFAULT 0,        -- 설치 횟수
    avg_rating DECIMAL(2,1),                -- 평균 평점 (향후)
    is_active BOOLEAN DEFAULT TRUE,         -- 공유 활성 상태
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(owner_username, skill_name)
);
```

#### skill_installs 테이블

```sql
CREATE TABLE skill_installs (
    id SERIAL PRIMARY KEY,
    skill_id INTEGER REFERENCES shared_skills(id),
    username VARCHAR(20) NOT NULL,          -- 설치한 사용자
    installed_at TIMESTAMP DEFAULT NOW(),
    uninstalled_at TIMESTAMP,               -- 제거 시 소프트 삭제
    UNIQUE(skill_id, username)              -- 중복 설치 방지
);
```

### 스킬 저장/공유 흐름

```
1. 사용자가 Pod에서 스킬 파일 작성
   ~/.claude/skills/db-report/SKILL.md

2. Hub "내 스킬" → "공유하기" 클릭
   → 스킬 파일을 EFS 공유 영역에 복사
   → EFS: /shared/skills/{owner}/{skill-name}/SKILL.md
   → DB: shared_skills 레코드 생성

3. 다른 사용자가 "스킬 스토어" → "설치" 클릭
   → EFS 공유 영역에서 사용자 Pod의 .claude/skills/로 복사
   → DB: skill_installs 레코드 생성, install_count++

4. "제거" 클릭
   → 사용자 Pod의 .claude/skills/{skill-name}/ 삭제
   → DB: uninstalled_at 설정, install_count--
```

### API 엔드포인트 (auth-gateway)

| 엔드포인트 | 메서드 | 기능 |
|-----------|--------|------|
| `/api/v1/skills/my` | GET | 내 스킬 목록 |
| `/api/v1/skills/store` | GET | 스토어 목록 (인기순, 검색, 필터) |
| `/api/v1/skills/installed` | GET | 설치된 스킬 목록 |
| `/api/v1/skills/publish` | POST | 스킬 스토어에 공유 등록 |
| `/api/v1/skills/{id}/install` | POST | 스킬 설치 |
| `/api/v1/skills/{id}/uninstall` | POST | 스킬 제거 |
| `/api/v1/skills/{id}` | PUT | 스킬 정보 수정 |
| `/api/v1/skills/{id}` | DELETE | 스킬 공유 해제 |

### fileserver.py 신규 API (Pod 내부)

| 엔드포인트 | 메서드 | 기능 |
|-----------|--------|------|
| `/api/skills/local` | GET | Pod의 `.claude/skills/` 디렉토리 스캔 |
| `/api/skills/upload` | POST | 스킬 파일을 EFS 공유 영역에 복사 |
| `/api/skills/download` | POST | EFS 공유 영역에서 Pod으로 스킬 복사 |
| `/api/skills/remove` | POST | Pod에서 설치된 스킬 삭제 |

### 향후 구현 (Future)

| 기능 | 설명 | 우선순위 |
|------|------|---------|
| **인기 랭킹** | install_count 기반 정렬 | Phase 3에 포함 |
| **개인 추천** | 사용자의 프롬프트 패턴 분석 → 유사 스킬 추천 | Phase 4 |
| **평점/리뷰** | 설치 후 별점 + 한줄평 | Phase 4 |
| **카테고리 분류** | 데이터조회/보고서/차트/업무자동화 | Phase 4 |
| **자동 업데이트** | 원본 수정 시 설치된 사용자에게 업데이트 알림 | Phase 4 |
| **스킬 버전 관리** | 스킬도 v1/v2/v3 관리 | Phase 4 |

---

## 15. 구현 Phase 계획

| Phase | 범위 | 주요 파일 |
|-------|------|----------|
| **Phase 1** | Hub UI 재설계 + 웹앱 멀티 관리 + 포트 자동 할당 + 통합 공유 관리 + 스크롤바 | fileserver.py, apps.py, k8s_service.py |
| **Phase 2** | 버전 관리 (deploy.sh 보강 + Hub 버전 모달) | deploy.sh, fileserver.py |
| **Phase 3** | 스킬 공유 체계 (스킬 스토어 + 설치/제거 + 인기 랭킹) | auth-gateway 신규 라우터, fileserver.py, DB 마이그레이션 |
| **Phase 4** (향후) | 스킬 추천 엔진 + 평점/리뷰 + 카테고리 + 자동 업데이트 | 별도 설계 |
