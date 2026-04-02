---
name: share
description: 파일, 데이터셋, 웹앱을 다른 사용자 또는 조직에게 공유합니다. 이름 또는 사번으로 공유 대상을 지정할 수 있습니다.
---

# 데이터/파일 공유

사용자의 파일, SQLite 데이터베이스, 디렉토리를 다른 사용자 또는 조직(담당/팀)에게 공유합니다.

## 공유 절차 — 반드시 API를 통해 수행

**절대 `.efs-users/` 경로에 직접 파일을 복사하지 마세요 (Read-only).**
반드시 아래 API를 호출하여 공유합니다.

### Step 1: 데이터셋 등록 (최초 1회)

공유하려는 파일/디렉토리를 먼저 데이터셋으로 등록합니다.

```python
import urllib.request, json, os

API = "http://auth-gateway.platform.svc.cluster.local"
POD = os.environ.get("HOSTNAME", "")

def api_call(path, method="GET", data=None):
    url = f"{API}/api/v1{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method,
        headers={"Content-Type": "application/json", "X-Pod-Name": POD})
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())

# 데이터셋 등록
result = api_call("/files/datasets", "POST", {
    "dataset_name": "tbm-2026",              # 고유 이름
    "file_path": "shared-data/TBM_2026.sqlite",  # workspace 내 상대 경로
    "file_type": "sqlite",                    # sqlite, excel, csv, directory
    "description": "2026년 1-2월 TBM 통합 데이터"
})
print(f"등록 완료: {result}")
```

### Step 2: 공유 대상 추가

이름 또는 사번으로 사용자를 검색한 후 공유합니다.

```python
# 사용자 검색 (이름 또는 사번)
members = api_call("/files/org-members?q=안병규")
print(members)  # {"members": [{"username": "N1103203", "name": "안병규", ...}]}

# 개인에게 공유
api_call("/files/datasets/tbm-2026/share", "POST", {
    "share_type": "user",
    "target": "N1103203"
})
print("안병규에게 공유 완료")

# 조직(팀) 전체에 공유
api_call("/files/datasets/tbm-2026/share", "POST", {
    "share_type": "team",
    "target": "HR팀"
})
print("HR팀 전체에 공유 완료")
```

### Step 3: 확인

```python
# 내 데이터셋 목록 확인
my_datasets = api_call("/files/datasets/my")
print(my_datasets)

# 특정 데이터셋의 공유 대상 확인
shares = api_call("/files/datasets/tbm-2026/share")
print(shares)
```

## 공유 대상 검색 방법

```python
# 이름으로 검색
api_call("/files/org-members?q=김광우")

# 직책으로 검색 (실장, 담당, 팀장)
api_call("/files/org-members?job=팀장")

# 담당 조직으로 검색
api_call("/files/org-members?region=경남Access담당")

# 팀으로 검색
api_call("/files/org-members?team=HR팀")

# 조합 검색
api_call("/files/org-members?team=HR팀&job=팀장")
```

## 공유 해제

```python
# 공유 목록에서 ID 확인
shares = api_call("/files/datasets/tbm-2026/share")
# → [{"id": 15, "share_type": "user", "share_target": "N1103203", ...}]

# 해제
api_call("/files/datasets/tbm-2026/share/15", "DELETE")
```

## 공유 후 상대방 접근

공유 설정 후 **60초 이내** 상대방의 Pod에 자동 심링크가 생성됩니다:
```
상대방 경로: ~/workspace/team/{내사번}/{데이터셋이름}/
```

## 주의사항

- `.efs-users/`에 직접 파일 복사 금지 (Read-only)
- 공유는 반드시 API를 통해 수행
- 공유된 데이터는 상대방에게 **읽기 전용**으로 제공
- Hub 포털(claude.skons.net/hub/)에서도 공유 관리 가능
