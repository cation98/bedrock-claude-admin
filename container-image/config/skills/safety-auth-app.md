---
name: safety-auth-app
description: 안전관리시스템(safeapi-green.skons.net) 2단계 인증(id/pw + SMS OTP)을 사용하는 Pod 웹앱 템플릿 생성. 특별 허용 사용자 전용.
allowed-users:
  - N1001063
---

# /safety-auth-app — 안전관리시스템 인증 웹앱 템플릿

## 🔒 접근 제한 (필수 사전 확인)

**이 스킬은 플랫폼 운영자가 특별 허용한 사용자에게만 제공됩니다.**

### 실행 전 MUST DO

1. 환경변수 `$USER_ID` 확인:
   ```bash
   echo "USER_ID=$USER_ID"
   ```

2. 허용된 사번 목록 (이 파일의 `allowed-users` 프런트매터):
   - `N1001063` (정병오)

3. `USER_ID` 가 허용 목록에 **없으면** 작업을 즉시 중단하고 반드시 다음 메시지로 응답하세요 (한국어, 그대로, 파일 생성 금지):

   > 죄송합니다. `/safety-auth-app` 스킬은 플랫폼 운영자가 **특별 허용한 사용자**만 사용할 수 있습니다 (현재 `N1001063` 정병오님 전용). 다른 사용자에게 사용 권한이 필요하면 관리자(N1102359)에게 요청해 주세요.

4. `USER_ID` 가 허용 목록에 **있으면** 아래 "구현 지침" 으로 진행하세요.

---

## 구현 지침 (허용 사용자 전용)

### 참고 합의 문서

안전관리시스템 팀과의 인증 연동 합의: `cation98/safety-management-system#34`.
아래 4가지 준수사항은 그 합의에 따른 **강제 규칙**입니다.

### 준수사항 (필수)

1. **`clientAuthKey`** — 매 로그인 시도마다 새 UUID 발급 (≤64자). 재사용 금지.
2. **`User-Agent` 헤더** — `BedrockPod/1.0 (<pod-hostname>)` 형식으로 모든 safeapi-green 호출에 설정.
3. **`X-Request-Source: bedrock-pod` 헤더** — 감사 로그 식별용 추가 헤더.
4. **로그아웃 시** `POST /auth/logout/` 호출 필수 (refresh 토큰 전달로 blacklist 처리).

### 사용 API (변경 없이 현행)

| Step | Endpoint | Body | Response |
|------|----------|-----|---------|
| 1 | `POST {SAFEAPI}/auth/login/` | `{username, password, clientAuthKey}` | `{username, status}` + 사용자 폰 SMS 발송 |
| 2 | `POST {SAFEAPI}/auth/verify_sms_code/` | `{username, code, clientAuthKey}` | `{access, refresh, userData}` |
| 3 | `POST {SAFEAPI}/auth/logout/` | `{refresh_token}` (Bearer access 포함) | 무효화 |

### 인증 스펙 (safety팀 확정)

| 항목 | 값 |
|------|-----|
| access_token 수명 | **12시간** |
| refresh 동작 | `POST /auth/login/refresh/` (표준 simplejwt) |
| SMS 코드 유효시간 | 300초 (5분) |
| `clientAuthKey` max length | 64자 |
| Rate limit | **username 기준** (IP 무관). 실패 시에만 카운트 (`FailOnlyThrottle`) |
| 기본 throttle | sms_verification 5/h, password_reset 10/h |
| 로그인 성공 시 throttle 영향 | **없음** (실패 카운트 리셋) |

### userData 에서 받는 사용자 정보 (UserProfileSerializer)

verify_sms_code 성공 응답의 `userData` 객체. Pod webapp 세션에 이 값 전체를 저장해 사용 가능:

**신원 / 연락**
- `id` (int) — auth_user.id
- `username` (str) — 사번
- `first_name` (str) — 사용자 이름 (암호화 필드에서 복호화된 값)
- `phone_number` (str) — 전화번호 (복호화된 값)

**조직 / 역할**
- `company_name` (str) — 회사 이름 (`sysmanage_companymaster.company_name`. 예: "SKO", BP사명)
- `region_name` (str) — 담당 지역명
- `team_name` (str) — 팀 이름
- `area` (str|null) — tregion 매칭된 지역
- `job_name` (str) — 직책/직무
- `role` (str) — 현재 role 문자열
- `default_role` (str) — 기본 role 이름 (StringRelatedField)
- `be_delegated_role` (str) — 위임받은 role
- `extra_role` (str[]) — 추가 role 배열

**계정 상태**
- `status` (str) — `정상` / `미승인` / `접근제한` / `탈퇴` / `장기미사용` / `퇴사`
- `is_superuser` (bool)
- `is_not_skt` (bool) — SKT 망 여부
- `is_onboarding_complete` (bool)
- `groups` (list) — Django group 목록

**약관 / 보안**
- `agree1_at`, `agree2_at`, `agree3_at` (datetime) — 약관 동의 시각
- `password_last_changed_at` (datetime)
- `days_since_change` (int) — 마지막 비번 변경 이후 경과일
- `is_password_reset` (bool) — 비번 재설정 필요 여부

### UX 주의 (safety팀 권고)

- `status == '장기미사용'` 또는 `'퇴사'` 응답의 `approval_request` 구조체는 **Pod webapp 에서는 처리하지 않음**. 간단 안내만 표시하고 "복구는 정식 안전관리 클라이언트(web/PWA)에서 진행" 으로 리다이렉트.
- 기타 `status` 의 `detail` 메시지는 그대로 화면에 노출 OK.
- `is_password_reset=True` 인 경우 대시보드 진입 전에 "정식 앱에서 비번 변경 필요" 안내.

### 프로젝트 구조

```
~/workspace/{name}/
├── app.py                  # FastAPI 2-step 로그인 + 대시보드
├── templates/
│   ├── login.html          # 사번/비번 폼
│   ├── verify.html         # SMS 인증코드 입력 폼
│   └── dashboard.html      # 로그인 완료 (userData 표시)
├── security_middleware.py  # /home/node/workspace/security_middleware.py 복사
├── robots.txt              # User-agent: *\nDisallow: /
├── requirements.txt        # fastapi uvicorn httpx jinja2 itsdangerous
└── README.md
```

### app.py 핵심 스켈레톤

```python
import os, uuid, httpx, json, secrets
from fastapi import FastAPI, Request, Response, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer, BadSignature

SAFEAPI = os.environ.get("SAFEAPI_URL", "https://safeapi-green.skons.net")
APP_SECRET = os.environ.get("APP_SECRET") or secrets.token_hex(32)
POD = os.environ.get("HOSTNAME", "unknown-pod")
UA = f"BedrockPod/1.0 ({POD})"
SAFETY_HEADERS = {
    "User-Agent": UA,
    "X-Request-Source": "bedrock-pod",
    "Content-Type": "application/json",
}
ser = URLSafeSerializer(APP_SECRET)
app = FastAPI()
templates = Jinja2Templates(directory="templates")

def get_session(request: Request) -> dict | None:
    token = request.cookies.get("session")
    if not token: return None
    try:
        return ser.loads(token)
    except BadSignature:
        return None

@app.get("/")
async def root(request: Request):
    if get_session(request):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(username: str = Form(), password: str = Form()):
    key = str(uuid.uuid4())  # 준수사항 1: 새 UUID
    async with httpx.AsyncClient(timeout=10) as cli:
        r = await cli.post(f"{SAFEAPI}/auth/login/",
                           json={"username": username, "password": password, "clientAuthKey": key},
                           headers=SAFETY_HEADERS)  # 준수사항 2, 3
    if r.status_code != 200:
        detail = r.json().get("detail", "로그인 실패")
        return HTMLResponse(f"<p>{detail}</p><a href='/'>다시</a>", 400)
    pending = ser.dumps({"username": username, "clientAuthKey": key})
    resp = RedirectResponse("/verify", status_code=303)
    resp.set_cookie("pending", pending, httponly=True, secure=True, max_age=300)
    return resp

@app.post("/verify")
async def verify(request: Request, code: str = Form()):
    p = ser.loads(request.cookies["pending"])
    async with httpx.AsyncClient(timeout=10) as cli:
        r = await cli.post(f"{SAFEAPI}/auth/verify_sms_code/",
                           json={"username": p["username"], "code": code, "clientAuthKey": p["clientAuthKey"]},
                           headers=SAFETY_HEADERS)
    if r.status_code != 200:
        return HTMLResponse(f"<p>{r.json().get('detail','인증 실패')}</p><a href='/verify'>다시</a>", 400)
    data = r.json()
    ud = data["userData"]
    session = ser.dumps({
        "username": ud["username"], "name": ud.get("first_name"),
        "company": ud.get("company_name"), "region": ud.get("region_name"),
        "team": ud.get("team_name"), "job": ud.get("job_name"),
        "role": ud.get("role"), "default_role": ud.get("default_role"),
        "extra_role": ud.get("extra_role", []),
        "access": data["access"], "refresh": data["refresh"],
    })
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie("session", session, httponly=True, secure=True, samesite="lax", max_age=12*3600)
    resp.delete_cookie("pending")
    return resp

@app.post("/logout")
async def logout(request: Request):
    s = get_session(request) or {}
    if s.get("refresh"):
        async with httpx.AsyncClient(timeout=5) as cli:
            await cli.post(f"{SAFEAPI}/auth/logout/",
                           json={"refresh_token": s["refresh"]},
                           headers={**SAFETY_HEADERS, "Authorization": f"Bearer {s.get('access','')}"})
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("session")
    return resp

@app.get("/dashboard")
async def dashboard(request: Request):
    s = get_session(request)
    if not s:
        return RedirectResponse("/")
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": s})
```

### 실행 명령

```bash
cd ~/workspace/{name}
pip install -r requirements.txt
python3 -m uvicorn app:app --host 0.0.0.0 --port 3000
```

접속: `https://claude.skons.net/app/$HOSTNAME/`

### 운영 주의

- **대량 동시 로그인 예상** (예: 교육·이벤트) 시 **D-1 안전관리팀 Slack 공지** — safety 측 요청사항
- SMS 비용 발생 (로그인 1회 = SMS 1건)
- `접근제한`, `탈퇴`, `미승인` 등 차단 상태에서의 detail 메시지는 그대로 노출 허용
- 세션 쿠키는 httpOnly + Secure 필수. 브라우저 JS 에서 access/refresh 를 읽을 수 없도록.
- 비밀번호는 메모리 외 어디에도 저장 금지 (로그/파일/DB 전부).

### 완료 후 사용자에게 안내

1. `~/workspace/{name}/` 생성 완료
2. 실행 방법 + 접속 URL
3. 본인 사번으로 로그인 테스트 → 폰으로 SMS 수신 → 6자리 코드 입력 → 대시보드 접속 확인
4. 이상 발생 시 safety-management-system#34 이슈 참조
