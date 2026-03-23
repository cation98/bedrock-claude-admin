---
name: webapp
description: 웹 애플리케이션을 만들어 포트 3000에서 실행합니다. Auth Gateway가 /app/ 경로를 프록시하므로 SSO 인증이 자동 적용됩니다.
---

# 웹앱 개발 스킬

사용자가 웹 애플리케이션(대시보드, 관리 화면, 데이터 시각화 등)을 요청하면 아래 절차를 따릅니다.

## 절차

1. **요구사항 분석**: 어떤 데이터를 보여줄지, 어떤 기능이 필요한지 파악
2. **프레임워크 선택**: FastAPI(백엔드) + HTML/JS 또는 단순 정적 페이지
3. **구현**: 포트 3000에서 실행되는 웹앱 작성
4. **실행**: 앱을 백그라운드로 실행

## 사용자 인증 (Auth Proxy 방식)

웹앱에서 현재 접속자를 확인하려면 Auth Gateway가 주입하는 헤더를 읽으세요.
SSO 코드를 직접 작성할 필요가 없습니다.

```python
from fastapi import Request, HTTPException

def get_current_user(request: Request) -> dict:
    """Auth Gateway가 주입한 인증 헤더에서 사용자 정보 추출"""
    user_id = request.headers.get("X-User-Id")
    if not user_id:
        raise HTTPException(401, "인증되지 않은 접근입니다")
    return {
        "user_id": user_id,
        "user_name": request.headers.get("X-User-Name", ""),
    }
```

- `X-User-Id`: 사용자 사번 (예: N1102359)
- `X-User-Name`: 사용자 이름
- Auth Gateway가 /app/ 경로의 모든 요청을 인증 후 전달합니다
- SSO_CLIENT_SECRET이 Pod에 노출되지 않아 안전합니다

## 접속 URL

웹앱은 Auth Gateway를 통해 아래 URL로 접근합니다:

```
https://claude.skons.net/app/{pod_name}/
```

Auth Gateway가 JWT를 검증한 뒤 Pod의 포트 3000으로 요청을 전달합니다.

## 웹앱 실행 방법

```bash
# FastAPI 예시
pip3 install fastapi uvicorn --quiet
uvicorn main:app --host 0.0.0.0 --port 3000 &

# Node.js/Express 예시
npx express-generator myapp && cd myapp
PORT=3000 npm start &
```

## FastAPI 웹앱 템플릿

```python
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
import subprocess
import os

app = FastAPI()

def get_current_user(request: Request) -> dict:
    """Auth Gateway가 주입한 인증 헤더에서 사용자 정보 추출"""
    user_id = request.headers.get("X-User-Id")
    if not user_id:
        raise HTTPException(401, "인증되지 않은 접근입니다")
    return {
        "user_id": user_id,
        "user_name": request.headers.get("X-User-Name", ""),
    }

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    return f"""
    <html>
    <head><title>My App</title></head>
    <body>
        <h1>안녕하세요, {user['user_name']}님!</h1>
        <p>사번: {user['user_id']}</p>
    </body>
    </html>
    """
```

## DB 연동

Pod에는 `$DATABASE_URL` 환경변수가 설정되어 있습니다.

```python
import os
import subprocess

# psql로 직접 조회
result = subprocess.run(
    ['psql', os.environ['DATABASE_URL'], '-t', '-A', '-F', ',', '-c', 'SELECT ...'],
    capture_output=True, text=True
)

# 또는 SQLAlchemy 사용
from sqlalchemy import create_engine
engine = create_engine(os.environ['DATABASE_URL'])
```

## 주의사항

- 반드시 포트 **3000**에서 실행해야 합니다 (Auth Gateway가 이 포트로 프록시)
- `0.0.0.0`에 바인딩해야 합니다 (`127.0.0.1`은 외부에서 접근 불가)
- 인증은 Auth Gateway가 처리하므로 웹앱에서 별도 로그인 구현 불필요
- SSO_CLIENT_SECRET을 Pod에서 직접 사용하지 마세요
