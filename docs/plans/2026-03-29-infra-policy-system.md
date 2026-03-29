# Infrastructure Policy System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 보안 정책에서 인프라 정책을 분리하여, 노드그룹별 허용 Pod 수량/CPU/Mem을 동적 관리하고, 개인 또는 그룹에 인프라 정책을 할당하는 시스템 구현. 인프라 페이지의 Pod 할당을 인프라 정책 기반으로 확장.

**Architecture:** infra_templates DB 테이블에 인프라 정책 템플릿을 저장 (노드그룹, max_pods_per_node, cpu_request, mem_request 등). users 테이블에 infra_policy JSONB 컬럼 추가로 개인별 인프라 정책 할당. k8s_service.py가 infra_policy 기반으로 Pod 리소스와 nodeSelector를 결정. 보안 정책의 node_tier를 제거하고 인프라 정책으로 이관.

**Tech Stack:** FastAPI, SQLAlchemy (JSONB), Next.js 15, Tailwind CSS, Kubernetes Python client

---

## Task 1: 인프라 정책 DB 모델 + 템플릿

**Files:**
- Create: `auth-gateway/app/models/infra_policy.py`
- Modify: `auth-gateway/app/models/user.py` (infra_policy 컬럼 추가)

**Step 1: InfraTemplate 모델 + InfraPolicy 스키마**

`auth-gateway/app/models/infra_policy.py`:
```python
"""인프라 정책 템플릿 — 노드그룹별 Pod 리소스 관리."""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, JSON
from app.core.database import Base

class InfraTemplate(Base):
    __tablename__ = "infra_templates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)  # "standard", "premium", "dedicated"
    description = Column(String(200))
    policy = Column(JSON, nullable=False)
    # policy structure:
    # {
    #   "nodegroup": "bedrock-claude-nodes" | "presenter-node",
    #   "node_selector": {"role": "presenter"} | null,
    #   "max_pods_per_node": 3,
    #   "cpu_request": "500m",
    #   "cpu_limit": "1000m",
    #   "memory_request": "1.5Gi",
    #   "memory_limit": "3Gi",
    #   "shared_dir_writable": false
    # }
    created_by = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                       onupdate=lambda: datetime.now(timezone.utc))

# 기본 인프라 정책 템플릿
INFRA_TEMPLATES = {
    "standard": {
        "nodegroup": "bedrock-claude-nodes",
        "node_selector": None,
        "max_pods_per_node": 3,
        "cpu_request": "500m",
        "cpu_limit": "1000m",
        "memory_request": "1.5Gi",
        "memory_limit": "3Gi",
        "shared_dir_writable": False,
    },
    "premium": {
        "nodegroup": "presenter-node",
        "node_selector": {"role": "presenter"},
        "max_pods_per_node": 1,
        "cpu_request": "3",
        "cpu_limit": "3500m",
        "memory_request": "8Gi",
        "memory_limit": "12Gi",
        "shared_dir_writable": True,
    },
    "shared-large": {
        "nodegroup": "bedrock-claude-nodes",
        "node_selector": None,
        "max_pods_per_node": 2,
        "cpu_request": "750m",
        "cpu_limit": "1500m",
        "memory_request": "3Gi",
        "memory_limit": "6Gi",
        "shared_dir_writable": False,
    },
}
```

**Step 2: users 테이블에 infra_policy 추가**

`user.py`에:
```python
infra_policy = Column(JSON, nullable=True, default=None)
# None = "standard" 기본값 적용
```

**Step 3: DB 마이그레이션**

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS infra_policy JSONB DEFAULT NULL;

CREATE TABLE IF NOT EXISTS infra_templates (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    description VARCHAR(200),
    policy JSONB NOT NULL,
    created_by VARCHAR(50),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

**Step 4: 커밋**
```bash
git commit -m "feat: InfraTemplate model + infra_policy user column + default templates"
```

---

## Task 2: 인프라 정책 CRUD API

**Files:**
- Create: `auth-gateway/app/routers/infra_policy.py`
- Modify: `auth-gateway/app/main.py`

**Step 1: 인프라 정책 라우터**

6개 엔드포인트:
```python
# GET  /api/v1/infra-policy/templates          — 전체 템플릿 (built-in + custom)
# POST /api/v1/infra-policy/templates          — custom 템플릿 생성
# PUT  /api/v1/infra-policy/templates/{id}     — 템플릿 수정
# DELETE /api/v1/infra-policy/templates/{id}   — 템플릿 삭제
# GET  /api/v1/infra-policy/assignments        — 사용자별 인프라 정책 할당 현황
# POST /api/v1/infra-policy/assign             — 사용자에게 인프라 정책 할당 (개별/일괄)
```

핵심 할당 API:
```python
@router.post("/assign")
async def assign_infra_policy(
    req: dict,  # {"usernames": ["N1102359"], "template_name": "premium"}
    _admin, db
):
    """사용자에게 인프라 정책 할당 (개별 또는 일괄)."""
    # 1. 템플릿 조회 (built-in 또는 custom)
    # 2. 각 사용자의 infra_policy 업데이트
    # 3. 감사 로그 기록
```

**Step 2: main.py 등록**
```python
from app.routers import infra_policy
app.include_router(infra_policy.router)
```

**Step 3: 커밋**
```bash
git commit -m "feat: infra policy CRUD API + user assignment endpoint"
```

---

## Task 3: k8s_service.py — infra_policy 기반 Pod 리소스 결정

**Files:**
- Modify: `auth-gateway/app/services/k8s_service.py`
- Modify: `auth-gateway/app/routers/sessions.py`

**Step 1: create_pod에서 infra_policy 사용**

security_policy에서 node_tier 제거, infra_policy에서 리소스 결정:

```python
def create_pod(self, ..., security_policy=None, infra_policy=None):
    # infra_policy에서 리소스 결정
    infra = infra_policy or INFRA_TEMPLATES["standard"]

    cpu_req = infra.get("cpu_request", "500m")
    cpu_lim = infra.get("cpu_limit", "1000m")
    mem_req = infra.get("memory_request", "1.5Gi")
    mem_lim = infra.get("memory_limit", "3Gi")
    node_selector = infra.get("node_selector")
    shared_writable = infra.get("shared_dir_writable", False)
```

**Step 2: sessions.py에서 infra_policy 전달**

```python
user_infra = user.infra_policy if (user and user.infra_policy) else INFRA_TEMPLATES["standard"]
pod_name = k8s.create_pod(..., security_policy=user_security, infra_policy=user_infra)
```

**Step 3: admin.py assign_pod/move_pod에도 infra_policy 전달**

**Step 4: security_policy에서 node_tier 제거**

schemas/security.py의 SECURITY_TEMPLATES에서 "node_tier" 키 제거.
SecurityPolicyData에서 node_tier 필드 제거.

**Step 5: _ensure_node_capacity에서 infra_policy 사용**

```python
def _ensure_node_capacity(username, security_policy=None, infra_policy=None):
    infra = infra_policy or INFRA_TEMPLATES["standard"]
    target_nodegroup = infra.get("nodegroup", "bedrock-claude-nodes")
    max_pods = infra.get("max_pods_per_node", 3)
    # nodegroup에 맞는 노드 검색 + max_pods 기준 체크
```

**Step 6: 커밋**
```bash
git commit -m "feat: infra_policy based Pod resource allocation, remove node_tier from security"
```

---

## Task 4: 인프라 페이지 재구성 — 정책 관리 + Pod 할당 확장

**Files:**
- Modify: `admin-dashboard/app/infra/page.tsx`
- Modify: `admin-dashboard/lib/api.ts`

**Step 1: API 함수 추가**

```typescript
// Infra Policy
export interface InfraTemplate {
  id?: number;
  name: string;
  description: string;
  policy: {
    nodegroup: string;
    node_selector: Record<string,string> | null;
    max_pods_per_node: number;
    cpu_request: string;
    cpu_limit: string;
    memory_request: string;
    memory_limit: string;
    shared_dir_writable: boolean;
  };
  isBuiltin?: boolean;
}

export interface InfraAssignment {
  user_id: number;
  username: string;
  name: string | null;
  infra_policy_name: string;
  infra_policy: Record<string,unknown>;
}

export function getInfraTemplates(): Promise<{templates: InfraTemplate[]}>;
export function createInfraTemplate(data): Promise<InfraTemplate>;
export function updateInfraTemplate(id, data): Promise<InfraTemplate>;
export function deleteInfraTemplate(id): Promise<{deleted:boolean}>;
export function getInfraAssignments(): Promise<{assignments: InfraAssignment[]}>;
export function assignInfraPolicy(data: {usernames:string[], template_name:string}): Promise<{assigned:number}>;
```

**Step 2: 인프라 페이지 레이아웃 재구성**

보안 정책 페이지와 유사한 2패널 구조:

```
┌─ 좌측 60%: 노드/Pod 현황 + 사용자 할당 ──┐ ┌─ 우측 40%: 인프라 정책 관리 ─────┐
│ ┌ 노드그룹 관리 (기존) ──────────────┐   │ │ 정책 목록 (스크롤)               │
│ │ bedrock-claude-nodes m5.large 0대  │   │ │ ├ standard (기본) — CPU 500m    │
│ │ presenter-node m5.xlarge 1대       │   │ │ ├ premium (기본) — CPU 3        │
│ │ system-node t3.medium 1대          │   │ │ ├ shared-large — CPU 750m      │
│ └────────────────────────────────────┘   │ │ └ [+ 새 정책 만들기]            │
│                                           │ │                                │
│ ┌ Pod 할당 ──────────────────────────┐   │ │ ┌─ 선택된 정책 상세 ──────────┐ │
│ │ [사용자 검색] [인프라정책▾] [할당]  │   │ │ │ 이름: standard              │ │
│ │ [N명 선택] [일괄 적용]             │   │ │ │ 노드그룹: bedrock-claude-nodes│
│ ├────────────────────────────────────┤   │ │ │ 노드당 Pod: [3]             │ │
│ │ ☐ 사용자  │ 인프라정책 │ 빠른설정  │   │ │ │ CPU Request: [500m]         │ │
│ │ ☐ 최종언  │ premium   │ [▾]       │   │ │ │ CPU Limit: [1000m]          │ │
│ │ ☐ 김민철  │ standard  │ [▾]       │   │ │ │ Mem Request: [1.5Gi]        │ │
│ │ ☐ 박창전  │ standard  │ [▾]       │   │ │ │ Mem Limit: [3Gi]            │ │
│ ├────────────────────────────────────┤   │ │ │ 공유 디렉토리 쓰기: [OFF]   │ │
│ │ ┌ ip-10-0-20-207 t3.medium ───┐   │   │ │ │ [저장] [삭제]               │ │
│ │ │ system (삭제 금지)           │   │   │ │ └─────────────────────────────┘ │
│ │ └─────────────────────────────┘   │   │ └────────────────────────────────┘
│ │ ┌ ip-10-0-10-182 m5.xlarge ───┐   │   │
│ │ │ 최종언 CPU 3/Mem 8Gi        │   │   │
│ │ └─────────────────────────────┘   │   │
│ └────────────────────────────────────┘   │
└───────────────────────────────────────────┘
```

**Step 3: 사용자 테이블에 인프라 정책 표시**

- 각 사용자의 현재 인프라 정책명 표시
- 빠른 설정 드롭다운에 모든 인프라 정책 목록
- 체크박스 선택 + 일괄 적용

**Step 4: 우측 정책 관리 패널**

- 보안 정책 페이지와 동일한 패턴
- built-in (standard/premium/shared-large) + custom
- 정책 클릭 → 상세 편집 (노드그룹, Pod수, CPU/Mem)
- 새 정책 만들기

**Step 5: 보안 정책 페이지에서 node_tier 제거**

admin-dashboard/app/security/page.tsx에서 "노드 등급" 필드셋 제거.

**Step 6: 커밋**
```bash
git commit -m "feat: infra policy management UI + pod assignment with policy selection"
```

---

## Task 5: 빌드 + 배포 + 검증

**Step 1: Auth Gateway 빌드 + 배포**
**Step 2: Admin Dashboard 빌드 + Amplify 배포**
**Step 3: Pod 재생성 (새 infra_policy 적용)**
**Step 4: 검증**

```
- standard 정책 (500m/1.5Gi, 노드당 3명) 확인
- premium 정책 (3CPU/8Gi, 노드당 1명) 확인
- custom 정책 생성 → 사용자 할당 → Pod 리소스 확인
- 보안 정책에서 node_tier 제거됨 확인
```

---

## 파일 변경 요약

| 파일 | 작업 | Task |
|------|------|------|
| `auth-gateway/app/models/infra_policy.py` | 새 파일 (InfraTemplate + 기본 템플릿) | 1 |
| `auth-gateway/app/models/user.py` | infra_policy 컬럼 추가 | 1 |
| `auth-gateway/app/routers/infra_policy.py` | 새 파일 (CRUD + 할당 API) | 2 |
| `auth-gateway/app/main.py` | infra_policy 라우터 등록 | 2 |
| `auth-gateway/app/services/k8s_service.py` | infra_policy 기반 리소스 결정 | 3 |
| `auth-gateway/app/routers/sessions.py` | infra_policy 전달 | 3 |
| `auth-gateway/app/routers/admin.py` | assign/move에 infra_policy 전달 | 3 |
| `auth-gateway/app/schemas/security.py` | node_tier 제거 | 3 |
| `admin-dashboard/app/infra/page.tsx` | 정책 관리 + Pod 할당 확장 | 4 |
| `admin-dashboard/app/security/page.tsx` | 노드 등급 필드 제거 | 4 |
| `admin-dashboard/lib/api.ts` | 인프라 정책 API 함수 | 4 |
