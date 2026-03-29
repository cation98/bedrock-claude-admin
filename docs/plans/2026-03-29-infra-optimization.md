# Infrastructure Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Pod 사양 축소(500m/1.5Gi), 노드 사양 선택(DB 기반), 업무시간 스케줄링(텔레그램 승인), 빈 노드 자동 축소를 구현하여 비용 73% 절감 + 운영 자동화.

**Architecture:** Pod CPU/Mem 요청을 축소하여 노드당 3명 수용. security_policy에 node_tier 필드 추가로 노드 사양을 DB 기반 선택. Lambda+EventBridge로 업무시간 스케줄링하고, 텔레그램 봇을 통한 연장 요청/승인 플로우. 빈 노드 자동 감지+축소로 비용 최적화.

**Tech Stack:** FastAPI, K8s Python client, boto3, Telegram Bot API, AWS Lambda, EventBridge

---

## Task 1: Pod 사양 축소 (500m/1.5Gi) + 노드당 3명 제한

**Files:**
- Modify: `auth-gateway/app/core/config.py`
- Modify: `auth-gateway/app/routers/sessions.py` (_ensure_node_capacity)

**Step 1: config.py에서 Pod 리소스 축소**

```python
# Before:
k8s_pod_cpu_request: str = "750m"
k8s_pod_memory_request: str = "2Gi"

# After:
k8s_pod_cpu_request: str = "500m"
k8s_pod_memory_request: str = "1.5Gi"
```

**Step 2: _ensure_node_capacity에 노드당 Pod 수 제한 추가**

sessions.py의 `_ensure_node_capacity()` 함수에서 CPU 여유 체크 외에 **사용자 Pod 수도 체크**:

```python
MAX_USER_PODS_PER_NODE = 3

# 각 노드의 사용자 Pod 수 확인
user_pods_on_node = len([
    p for p in pods
    if p.metadata.labels.get("app") == "claude-terminal"
])

if user_pods_on_node >= MAX_USER_PODS_PER_NODE:
    # 이 노드 포화 → 다음 노드 확인 또는 스케일업
    continue
```

**Step 3: 커밋**

```bash
git commit -m "feat: reduce Pod to 500m/1.5Gi + limit 3 user pods per node"
```

---

## Task 2: 노드 사양 선택 DB 기반 (하드코딩 제거)

**Files:**
- Modify: `auth-gateway/app/schemas/security.py` (SECURITY_TEMPLATES에 node_tier 추가)
- Modify: `auth-gateway/app/services/k8s_service.py` (PRESENTER_USERS 하드코딩 제거)
- Modify: `admin-dashboard/app/security/page.tsx` (노드 등급 UI 추가)

**Step 1: security_policy에 node_tier 추가**

schemas/security.py SECURITY_TEMPLATES 각 등급에:
```python
"node_tier": "standard",  # "standard" = m5.large, "premium" = m5.xlarge
```

**Step 2: k8s_service.py에서 하드코딩 제거**

```python
# Before:
PRESENTER_USERS = {"N1102359", "N1001065"}
node_selector={"role": "presenter"} if username.upper() in PRESENTER_USERS else None

# After:
node_tier = (security_policy or {}).get("node_tier", "standard")
if node_tier == "premium":
    node_selector = {"role": "presenter"}
    cpu_request = "3"
    memory_request = "8Gi"
else:
    node_selector = None  # 일반 노드
    cpu_request = self.settings.k8s_pod_cpu_request
    memory_request = self.settings.k8s_pod_memory_request
```

**Step 3: Admin 보안 정책 페이지에 노드 등급 선택 추가**

정책 편집 패널에:
```tsx
<fieldset>
  <legend>노드 등급</legend>
  <select value={editNodeTier} onChange={...}>
    <option value="standard">Standard (m5.large)</option>
    <option value="premium">Premium (m5.xlarge)</option>
  </select>
</fieldset>
```

**Step 4: 커밋**

```bash
git commit -m "feat: DB-based node_tier selection, remove hardcoded PRESENTER_USERS"
```

---

## Task 3: 업무시간 스케줄링 — 종료 경고 + 텔레그램 연장 요청/승인

**Files:**
- Create: `auth-gateway/app/routers/scheduling.py`
- Modify: `auth-gateway/app/routers/telegram.py` (연장 명령어 추가)
- Modify: `auth-gateway/app/main.py` (라우터 등록)

**Step 1: 스케줄링 API 생성**

scheduling.py:
```python
"""업무시간 스케줄링 + 연장 요청/승인."""

@router.post("/schedule/shutdown-warning")
async def send_shutdown_warning(minutes_before: int = 30):
    """종료 N분 전 경고 발송 (Lambda/CronJob에서 호출)."""
    # 모든 running Pod 사용자에게 텔레그램 알림
    # "⚠ {minutes_before}분 후 세션이 종료됩니다. /연장요청 으로 추가 시간을 요청하세요."

@router.post("/schedule/shutdown")
async def execute_shutdown():
    """업무시간 종료 — 미연장 Pod 종료 + 노드 축소."""
    # 연장 승인된 사용자 제외, 나머지 Pod backup + 종료
    # 빈 노드 desiredSize 축소

@router.post("/schedule/startup")
async def execute_startup(desired_nodes: int = 2):
    """업무시간 시작 — 노드 확장."""
    # bedrock-claude-nodes desiredSize 설정

@router.post("/extension/request")
async def request_extension(username: str, hours: int = 2):
    """연장 요청 → 관리자에게 텔레그램 알림."""
    # extension_requests 테이블에 기록
    # 관리자 텔레그램에 승인 요청 발송

@router.post("/extension/approve")
async def approve_extension(username: str, admin: str):
    """연장 승인 → Pod TTL 연장 + 사용자 알림."""
```

**Step 2: 텔레그램 봇에 연장 명령어 추가**

telegram.py에:
```python
# /연장요청 — 사용자가 연장 요청
# /승인 N1001065 — 관리자가 승인
# /거절 N1001065 — 관리자가 거절
```

**Step 3: extension_requests DB 테이블**

```sql
CREATE TABLE extension_requests (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    requested_hours INTEGER DEFAULT 2,
    status VARCHAR(20) DEFAULT 'pending',  -- pending, approved, rejected
    requested_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at TIMESTAMP WITH TIME ZONE,
    resolved_by VARCHAR(50)
);
```

**Step 4: 커밋**

```bash
git commit -m "feat: business hours scheduling + telegram extension request/approve"
```

---

## Task 4: 빈 노드 자동 축소

**Files:**
- Modify: `auth-gateway/app/routers/admin.py` (자동 축소 엔드포인트)
- Modify: `auth-gateway/app/routers/scheduling.py` (축소 로직 통합)

**Step 1: 빈 노드 감지 + 축소 API**

admin.py에:
```python
@router.post("/auto-scale-down")
async def auto_scale_down():
    """사용자 Pod 없는 노드 자동 축소 (system-node 제외)."""
    # 1. 모든 노드 조회
    # 2. 사용자 Pod 0개인 노드 식별 (system/ingress Pod 제외)
    # 3. 해당 노드 cordon
    # 4. 노드그룹 desiredSize -1
    # system-node는 절대 축소 안 함
```

**Step 2: 30분 주기 체크 (scheduling.py에서 호출)**

```python
# scheduling.py의 shutdown에서 호출
# 또는 별도 CronJob/Lambda에서 30분마다 호출
await auto_scale_down()
```

**Step 3: 커밋**

```bash
git commit -m "feat: auto scale-down empty nodes (protect system-node)"
```

---

## Task 5: AWS EventBridge + Lambda 스케줄 설정

**Files:**
- Create: `infra/lambda/schedule-handler.py`
- Create: `infra/terraform/eventbridge.tf` (또는 AWS CLI 명령)

**Step 1: Lambda 함수 (또는 AWS CLI로 직접 설정)**

```python
# schedule-handler.py
import urllib.request, json

AUTH_GATEWAY_URL = "https://claude.skons.net"
ADMIN_TOKEN = "..."  # Secrets Manager에서

def handler(event, context):
    action = event.get("action")

    if action == "startup":
        # POST /api/v1/admin/schedule/startup
    elif action == "warning-30":
        # POST /api/v1/admin/schedule/shutdown-warning?minutes_before=30
    elif action == "warning-15":
        # POST /api/v1/admin/schedule/shutdown-warning?minutes_before=15
    elif action == "shutdown":
        # POST /api/v1/admin/schedule/shutdown
```

**Step 2: EventBridge 스케줄 규칙**

```
평일 09:00 KST (00:00 UTC) → action=startup
평일 17:30 KST (08:30 UTC) → action=warning-30
평일 17:45 KST (08:45 UTC) → action=warning-15
평일 18:00 KST (09:00 UTC) → action=shutdown
```

**Step 3: 커밋**

```bash
git commit -m "infra: EventBridge + Lambda for business hours scheduling"
```

---

## Task 6: 전체 빌드 + 배포 + 검증

**Step 1: Auth Gateway 빌드 + 배포**
**Step 2: Terminal Image 빌드 + 배포 (변경 시)**
**Step 3: Admin Dashboard 빌드 + Amplify 배포**
**Step 4: Pod 재생성 (새 사양 적용)**
**Step 5: 검증**

```
- m5.large 노드에 3명 Pod 생성 확인
- premium 사용자는 m5.xlarge에 배치 확인
- 텔레그램 /연장요청 + /승인 동작 확인
- 빈 노드 자동 축소 확인
```

---

## 파일 변경 요약

| 파일 | 작업 | Task |
|------|------|------|
| `auth-gateway/app/core/config.py` | Pod 사양 축소 | 1 |
| `auth-gateway/app/routers/sessions.py` | 노드당 Pod 수 제한 | 1 |
| `auth-gateway/app/schemas/security.py` | node_tier 추가 | 2 |
| `auth-gateway/app/services/k8s_service.py` | 하드코딩 제거 + node_tier 분기 | 2 |
| `admin-dashboard/app/security/page.tsx` | 노드 등급 UI | 2 |
| `auth-gateway/app/routers/scheduling.py` | 스케줄링 API (새 파일) | 3 |
| `auth-gateway/app/routers/telegram.py` | 연장 명령어 | 3 |
| `auth-gateway/app/routers/admin.py` | 자동 축소 API | 4 |
| `infra/lambda/schedule-handler.py` | Lambda 핸들러 | 5 |
