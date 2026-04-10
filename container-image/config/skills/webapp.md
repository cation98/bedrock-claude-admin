---
name: webapp
description: 웹 애플리케이션을 만들어 포트 3000에서 실행합니다. 배포 시 Auth Gateway가 SSO 인증 + 사용자 프로필 헤더를 자동 주입합니다.
---

# 웹앱 개발 스킬

사용자가 웹 애플리케이션(대시보드, 관리 화면, 데이터 시각화 등)을 요청하면 아래 절차를 따릅니다.

## 절차

1. **요구사항 분석**: 어떤 데이터를 보여줄지, 어떤 기능이 필요한지 파악
2. **프레임워크 선택**: FastAPI(백엔드) + HTML/JS 또는 단순 정적 페이지
3. **구현**: 포트 3000에서 실행되는 웹앱 작성
4. **실행**: 앱을 백그라운드로 실행
5. **배포**: `deploy 앱이름`으로 플랫폼에 배포 (사용자 프로필 헤더 활성화)

## 로그인 사용자 정보 획득 (배포된 앱)

배포된 앱(`deploy` 명령으로 배포)은 Auth Gateway가 매 요청마다 SSO 인증 후 사용자 프로필 헤더를 주입합니다. 웹앱에서 이 헤더를 읽으면 로그인 사용자 정보를 즉시 사용할 수 있습니다.

### 제공되는 헤더

| 헤더 | 설명 | 예시 (URL-encoded) |
|------|------|---------------------|
| `X-Auth-Username` | 사번 | `N1102359` |
| `X-Auth-Name` | 이름 | `%EC%B5%9C%EC%A2%85%EC%96%B8` (최종언) |
| `X-Auth-Team` | 팀명 | `%ED%92%88%EC%A7%88%ED%98%81%EC%8B%A0%ED%8C%80` |
| `X-Auth-Region` | 지역 | `%EA%B2%BD%EB%82%A8%EB%8B%B4%EB%8B%B9` |
| `X-Auth-Job` | 직책 | `%EB%A7%A4%EB%8B%88%EC%A0%80` |

**주의: 한글 값은 URL-encoded 상태로 전달됩니다. 반드시 `urllib.parse.unquote()`로 디코딩하세요.**

### FastAPI 사용자 정보 추출 함수

```python
from fastapi import Request, HTTPException
from urllib.parse import unquote

def get_current_user(request: Request) -> dict:
    """Auth Gateway가 주입한 인증 헤더에서 사용자 프로필 추출.
    
    배포된 앱에서만 동작합니다. 로컬 개발 중에는 헤더가 없을 수 있습니다.
    """
    username = request.headers.get("X-Auth-Username")
    if not username:
        raise HTTPException(401, "인증되지 않은 접근입니다")
    return {
        "username": username,
        "name": unquote(request.headers.get("X-Auth-Name", "")),
        "team": unquote(request.headers.get("X-Auth-Team", "")),
        "region": unquote(request.headers.get("X-Auth-Region", "")),
        "job": unquote(request.headers.get("X-Auth-Job", "")),
    }
```

### 완전한 FastAPI 템플릿 (사용자 프로필 표시)

```python
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from urllib.parse import unquote
import os

app = FastAPI()
HOSTNAME = os.environ.get("HOSTNAME", "unknown")

def get_current_user(request: Request) -> dict:
    username = request.headers.get("X-Auth-Username")
    if not username:
        raise HTTPException(401, "인증되지 않은 접근입니다")
    return {
        "username": username,
        "name": unquote(request.headers.get("X-Auth-Name", "")),
        "team": unquote(request.headers.get("X-Auth-Team", "")),
        "region": unquote(request.headers.get("X-Auth-Region", "")),
        "job": unquote(request.headers.get("X-Auth-Job", "")),
    }

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    return f"""
    <html>
    <head>
        <title>My App</title>
        <base href="/app/{HOSTNAME}/">
    </head>
    <body>
        <h1>안녕하세요, {user['name']}님!</h1>
        <p>사번: {user['username']}</p>
        <p>부서: {user['team']}</p>
        <p>지역: {user['region']}</p>
        <p>직책: {user['job']}</p>
    </body>
    </html>
    """

# 실행: python3 -m uvicorn app:app --host 0.0.0.0 --port 3000
# 배포: deploy my-app
# 접속: https://claude.skons.net/apps/{slug}/my-app/
```

### Node.js/Express 사용자 정보 추출

```javascript
function getCurrentUser(req) {
    const username = req.headers['x-auth-username'];
    if (!username) throw new Error('Unauthorized');
    return {
        username,
        name: decodeURIComponent(req.headers['x-auth-name'] || ''),
        team: decodeURIComponent(req.headers['x-auth-team'] || ''),
        region: decodeURIComponent(req.headers['x-auth-region'] || ''),
        job: decodeURIComponent(req.headers['x-auth-job'] || ''),
    };
}

app.get('/', (req, res) => {
    const user = getCurrentUser(req);
    res.send(`안녕하세요, ${user.name}님! (${user.team})`);
});
```

## 접속 URL

### 배포된 앱 (사용자 프로필 제공)
```
https://claude.skons.net/apps/{slug}/{app-name}/
```
- `deploy 앱이름`으로 배포 후 접근
- Auth Gateway가 SSO 인증 + ACL 검증 + 사용자 프로필 헤더 주입
- ACL로 접근 제어 가능 (개인/팀/지역/직책/전사)

### 로컬 개발 중 (사용자 프로필 미제공)
```
https://claude.skons.net/app/{pod-name}/
```
- 포트 3000으로 실행 중인 앱에 직접 접근
- 사용자 프로필 헤더 없음 (개발/테스트 용도)

## 웹앱 실행 방법

```bash
# FastAPI
pip3 install fastapi uvicorn --quiet
python3 -m uvicorn app:app --host 0.0.0.0 --port 3000 &

# Node.js/Express
npx express-generator myapp && cd myapp
PORT=3000 npm start &
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
- 한글 헤더 값은 반드시 URL-decode (`unquote()` / `decodeURIComponent()`)
