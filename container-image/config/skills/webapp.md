---
name: webapp
description: 대시보드, 웹앱을 구현하고 사용자가 브라우저에서 접속할 수 있도록 안내합니다. 대시보드, 웹앱, 차트, 시각화, 모니터링 화면 요청 시 사용합니다.
---

# 웹앱/대시보드 구현 스킬

사용자가 대시보드, 웹앱, 시각화 화면 등을 요청하면 이 절차를 따릅니다.

## 필수 규칙 — 반드시 준수

### 1. 포트는 반드시 3000번 사용

```bash
# ✅ 올바름
python3 -m uvicorn app:app --host 0.0.0.0 --port 3000

# ❌ 금지 (외부 접속 불가)
python3 -m uvicorn app:app --port 8000
python3 -m uvicorn app:app --port 8200
```

포트 3000만 Ingress를 통해 외부 브라우저에서 접속 가능합니다.

### 2. 접속 URL

```
https://claude.skons.net/app/$HOSTNAME/
```

`$HOSTNAME` 환경변수에 Pod 이름이 들어 있습니다 (예: `claude-terminal-n1102359`).

### 3. 사전 설치된 패키지 (pip 설치 불필요)

- fastapi, uvicorn[standard]
- psycopg2-binary
- jinja2, python-multipart
- pandas, matplotlib, openpyxl

## 구현 절차

### Step 1: 데이터 확인
```bash
# TANGO 알람 DB
psql-tango -c "쿼리"

# Safety DB
psql $DATABASE_URL -c "쿼리"
```

### Step 2: 프로젝트 생성
```bash
mkdir -p ~/workspace/{프로젝트명}/templates
cd ~/workspace/{프로젝트명}
```

### Step 3: 앱 작성 (FastAPI 기본 구조)
```python
# app.py
import os
import json
from datetime import date
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
import psycopg2
import psycopg2.extras

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def get_db_connection(db_type="safety"):
    """DB 연결. db_type: 'safety' 또는 'tango'"""
    if db_type == "tango":
        return psycopg2.connect(
            host="aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com",
            database="postgres", user="claude_readonly",
            password="TangoReadOnly2026", sslmode="require"
        )
    else:
        return psycopg2.connect(os.environ["DATABASE_URL"])

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    conn = get_db_connection("safety")  # 또는 "tango"
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT ...")
    rows = cur.fetchall()
    conn.close()
    return templates.TemplateResponse(request, "index.html", {"rows": rows})
```

주의: Jinja2 템플릿에서 `tojson` 필터 대신 서버에서 `json.dumps()` 후 `{{ var | safe }}`를 사용하세요.

### Step 4: 기존 프로세스 정리 후 실행
```bash
pkill -f "uvicorn" 2>/dev/null; sleep 1
cd ~/workspace/{프로젝트명}
DATABASE_URL="$DATABASE_URL" python3 -m uvicorn app:app --host 0.0.0.0 --port 3000 &
```

### Step 5: 동작 확인
```bash
sleep 3
curl -s http://localhost:3000/ | head -5
```

### Step 6: 사용자에게 접속 안내 (반드시 포함)

웹앱 구현 완료 후 반드시 아래 형식으로 접속 방법을 안내하세요:

```
대시보드가 준비되었습니다.

접속 방법:
1. 허브 페이지의 [웹앱] 버튼을 클릭하세요
2. 또는 브라우저에서 직접 접속: https://claude.skons.net/app/{$HOSTNAME}/

재시작 명령어:
  cd ~/workspace/{프로젝트명}
  DATABASE_URL="$DATABASE_URL" python3 -m uvicorn app:app --host 0.0.0.0 --port 3000 &
```

## URL/경로 규칙 — 반드시 준수 (무한 리다이렉트 방지)

웹앱은 Ingress 뒤에서 실행되며 URL이 재작성됩니다:
- 브라우저 URL: `https://claude.skons.net/app/{pod_name}/something`
- 웹앱이 받는 URL: `/something`

**폼, 링크, API 호출에서 절대 경로(`/`)를 사용하면 Auth Gateway 로그인 페이지로 이동합니다!**

### HTML 템플릿에 반드시 base 태그 추가

```html
<head>
  <base href="/app/{{ hostname }}/">
  ...
</head>
```

`hostname`은 서버에서 `os.environ.get("HOSTNAME")`으로 전달합니다.

### 폼/링크는 상대 경로 사용

```html
<!-- ✅ 올바름 (상대 경로) -->
<form action="">
<form action="?period=14d&team=all">
<a href="?page=2">

<!-- ❌ 금지 (절대 경로 → 로그인 페이지로 이동) -->
<form action="/">
<form action="/?period=14d">
<a href="/">
```

### JavaScript fetch/API 호출도 상대 경로

```javascript
// ✅ 올바름
fetch("api/data")
fetch("?period=14d")

// ❌ 금지
fetch("/api/data")
fetch("/")
```

### FastAPI 앱에서 hostname 전달

```python
import os

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "hostname": os.environ.get("HOSTNAME", ""),
        # ... 다른 데이터
    })
```

## 템플릿 작성 주의사항

1. **CDN 스크립트 사용**: Chart.js 등은 CDN으로 로드 (npm 설치 불필요)
   ```html
   <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
   ```

2. **Jinja2 tojson 대신 서버 JSON**:
   ```python
   # app.py에서
   import json
   labels_json = json.dumps(labels, ensure_ascii=False)
   # 템플릿에서
   const labels = {{ labels_json | safe }};
   ```

3. **round() 대신 format()**:
   ```jinja2
   {# ❌ #} {{ value | round(1) }}
   {# ✅ #} {{ "%.1f" | format(value) }}
   ```

4. **TemplateResponse 인자 순서** (Starlette 1.0+):
   ```python
   # ✅ request가 첫 번째
   return templates.TemplateResponse(request, "index.html", {변수들})
   # ❌ 구버전 방식
   return templates.TemplateResponse("index.html", {"request": request, ...})
   ```

## TANGO DB 연결 (대시보드용)

psql-tango 명령어는 셸에서만 사용 가능합니다. Python 코드에서는 직접 연결하세요:

```python
def get_tango_connection():
    return psycopg2.connect(
        host="aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com",
        database="postgres",
        user="claude_readonly",
        password="TangoReadOnly2026",
        sslmode="require"
    )
```
