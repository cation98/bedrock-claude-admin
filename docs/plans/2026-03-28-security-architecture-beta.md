# Per-User Security Architecture Implementation Plan (Beta)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Claude Code 사내 플랫폼을 대표이사/임원/팀장/실무자까지 사용 가능한 상용 베타 시스템으로 전환하기 위한 보안 아키텍처 구현. SMS 2FA 인증, 사용자별 DB/테이블 접근 제어, 스킬 필터링, CLAUDE.md 동적 생성, PostgreSQL 역할 분리, Pod 보안 강화를 포함하는 8층 방어 체계.

**Architecture:** 로그인 시 SSO 인증 후 SMS 2FA(6자리 코드, 5분 만료, 5회 실패 잠금)를 거쳐야 JWT가 발급된다. O-Guard 프로젝트의 2FA 패턴(TwoFactorCode 모델, generate_verification_code, verify_code)을 재사용한다. users 테이블에 security_policy JSONB 컬럼을 추가하여 사용자별 보안 등급(basic/standard/full)과 세부 권한을 저장한다. Pod 생성 시 보안 정책을 환경변수로 주입하고, entrypoint.sh가 이를 파싱하여 CLAUDE.md(모듈형 섹션 조립), .pgpass(허용 DB만), settings.json(등급별 deny list), 스킬(허용분만 유지)을 동적으로 생성한다. PostgreSQL에 등급별 역할을 분리하고, PreToolUse hooks로 위험 명령을 실시간 차단한다.

**Tech Stack:** FastAPI, SQLAlchemy (JSONB), Next.js 15, Tailwind CSS, Kubernetes Python client, boto3, PostgreSQL GRANT/REVOKE, Claude Code hooks/settings.json, SMS API (KT 알뜰통신)

---

## Phase 0: SMS 2FA 인증 추가 (로그인 보안 강화)

### Task 0-1: TwoFactorCode 모델 + 2FA 서비스 생성

**Files:**
- Create: `auth-gateway/app/models/two_factor_code.py`
- Create: `auth-gateway/app/services/two_factor_service.py`

**Step 1: TwoFactorCode 모델 생성 (O-Guard 패턴 재사용)**

`auth-gateway/app/models/two_factor_code.py`:

```python
"""SMS 2차 인증 코드 관리."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, Boolean, Index
from app.core.database import Base

class TwoFactorCode(Base):
    __tablename__ = "two_factor_codes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(50), nullable=False)
    code = Column(String(6), nullable=False)
    phone_number = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    verified = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("idx_2fa_username_created", "username", "created_at"),
    )
```

**Step 2: 2FA 서비스 생성**

`auth-gateway/app/services/two_factor_service.py`:

```python
"""2차 인증 코드 생성, 검증, 계정 잠금."""
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.two_factor_code import TwoFactorCode

logger = logging.getLogger(__name__)

CODE_EXPIRY_MINUTES = 5
MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


class TwoFactorError(Exception):
    pass

class CodeExpiredError(TwoFactorError):
    pass

class CodeInvalidError(TwoFactorError):
    pass

class MaxAttemptsError(TwoFactorError):
    pass

class AccountLockedError(TwoFactorError):
    def __init__(self, message: str, remaining_seconds: int):
        super().__init__(message)
        self.remaining_seconds = remaining_seconds


def generate_code(username: str, phone_number: str, db: Session) -> str:
    """6자리 인증코드 생성 + DB 저장. code_id 반환."""
    code = str(secrets.randbelow(1000000)).zfill(6)
    now = datetime.now(timezone.utc)
    code_id = str(uuid.uuid4())

    record = TwoFactorCode(
        id=code_id,
        username=username,
        code=code,
        phone_number=phone_number,
        created_at=now,
        expires_at=now + timedelta(minutes=CODE_EXPIRY_MINUTES),
    )
    db.add(record)
    db.commit()

    logger.info(f"2FA code generated for {username}: code_id={code_id}")
    return code_id, code


def verify_code(code_id: str, input_code: str, db: Session) -> bool:
    """인증코드 검증. 성공 시 True, 실패 시 attempts 증가."""
    record = db.query(TwoFactorCode).filter(TwoFactorCode.id == code_id).first()
    if not record:
        raise TwoFactorError("인증 요청을 찾을 수 없습니다")

    now = datetime.now(timezone.utc)
    if now > record.expires_at:
        raise CodeExpiredError("인증코드가 만료되었습니다. 다시 로그인해주세요.")

    if record.attempts >= MAX_ATTEMPTS:
        raise MaxAttemptsError("최대 인증 시도 횟수를 초과했습니다.")

    if record.verified:
        raise TwoFactorError("이미 인증된 코드입니다.")

    if record.code != input_code:
        record.attempts += 1
        db.commit()
        remaining = MAX_ATTEMPTS - record.attempts
        if remaining <= 0:
            raise MaxAttemptsError("최대 인증 시도 횟수를 초과했습니다.")
        raise CodeInvalidError(f"인증코드가 일치하지 않습니다. ({remaining}회 남음)")

    record.verified = True
    db.commit()
    return True


def check_lockout(username: str, db: Session) -> None:
    """최근 LOCKOUT_MINUTES 내 MAX_ATTEMPTS 실패 시 잠금."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_MINUTES)
    failed = db.query(TwoFactorCode).filter(
        TwoFactorCode.username == username,
        TwoFactorCode.created_at >= cutoff,
        TwoFactorCode.verified == False,
        TwoFactorCode.attempts >= MAX_ATTEMPTS,
    ).count()

    if failed >= 3:  # 최근 15분 내 3건 이상 코드에서 5회씩 실패
        raise AccountLockedError(
            f"계정이 잠겼습니다. {LOCKOUT_MINUTES}분 후 다시 시도해주세요.",
            remaining_seconds=LOCKOUT_MINUTES * 60,
        )
```

**Step 3: 커밋**

```bash
git add auth-gateway/app/models/two_factor_code.py auth-gateway/app/services/two_factor_service.py
git commit -m "feat: add TwoFactorCode model + 2FA service (generate, verify, lockout)"
```

---

### Task 0-2: Auth 라우터에 2FA 엔드포인트 추가

**Files:**
- Modify: `auth-gateway/app/routers/auth.py`

**Step 1: 로그인 플로우를 2단계로 분리**

현재 플로우: `POST /login` → SSO 인증 → JWT 즉시 발급
변경 플로우: `POST /login` → SSO 인증 → SMS 발송 → code_id 반환
           `POST /verify-2fa` → 코드 검증 → JWT 발급

`auth.py`에 추가:

```python
from app.services.two_factor_service import (
    generate_code, verify_code, check_lockout,
    TwoFactorError, CodeExpiredError, CodeInvalidError,
    MaxAttemptsError, AccountLockedError,
)
from app.models.two_factor_code import TwoFactorCode


class LoginStep1Response(BaseModel):
    """2FA 코드 발송 응답."""
    code_id: str
    phone_masked: str  # "010-****-1234"
    message: str = "인증코드가 발송되었습니다"


class Verify2faRequest(BaseModel):
    code_id: str
    code: str  # 6자리


# 기존 login() 함수 수정:
# SSO 인증 성공 후 JWT 발급 대신 → SMS 발송 + code_id 반환
# phone_number는 SSO userinfo 또는 O-Guard DB에서 조회

@router.post("/login", response_model=LoginStep1Response)
async def login_step1(...):
    """Step 1: SSO 인증 + SMS 2FA 코드 발송."""
    # 1. SSO 인증 (기존 로직 유지)
    # 2. 계정 잠금 체크
    check_lockout(username, db)
    # 3. 전화번호 조회 (user.phone_number 또는 O-Guard DB)
    phone = user.phone_number or _fetch_phone_from_oguard(username, settings)
    # 4. 6자리 코드 생성 + SMS 발송
    code_id, code = generate_code(username, phone, db)
    _send_2fa_sms(phone, code, settings)
    # 5. 마스킹된 전화번호 + code_id 반환
    masked = phone[:3] + "-****-" + phone[-4:]
    return LoginStep1Response(code_id=code_id, phone_masked=masked)


@router.post("/verify-2fa", response_model=LoginResponse)
async def login_step2_verify(...):
    """Step 2: 2FA 코드 검증 + JWT 발급."""
    # 1. 코드 검증
    verify_code(request.code_id, request.code, db)
    # 2. code_id에서 username 추출
    record = db.query(TwoFactorCode).filter(TwoFactorCode.id == request.code_id).first()
    username = record.username
    # 3. JWT 발급 (기존 로직)
    user = db.query(User).filter(User.username == username).first()
    token = create_access_token({"sub": user.username, "user_id": user.id, "role": user.role}, settings)
    return LoginResponse(access_token=token, username=user.username, name=user.name, role=user.role)


def _send_2fa_sms(phone: str, code: str, settings: Settings):
    """기존 SMS 라우터의 발송 로직 재사용."""
    import httpx
    message = f"[Claude Code] 인증코드: {code} (5분 이내 입력)"
    # 기존 sms.py의 SMS API 호출 로직 재사용
    # KT 알뜰통신 API: POST to sms_api_url
```

**Step 2: 관리자/바이패스 사용자는 2FA 스킵 옵션**

```python
# 워크숍 바이패스 사용자는 2FA 없이 직접 JWT 발급
WORKSHOP_BYPASS = {"N1001048": "claude2026", "N1001059": "claude2026"}
if bypass_pw and request.password == bypass_pw:
    # 바이패스 로그인: 2FA 스킵, JWT 즉시 발급
    return LoginResponse(access_token=token, ...)

# Admin 설정으로 2FA 전체 on/off 가능 (초기 배포 시 테스트용)
# settings에 TWO_FACTOR_ENABLED=true/false 추가
```

**Step 3: 커밋**

```bash
git add auth-gateway/app/routers/auth.py
git commit -m "feat: split login into 2 steps - SSO auth + SMS 2FA verification"
```

---

### Task 0-3: 로그인 페이지 2FA UI 추가

**Files:**
- Modify: `auth-gateway/app/static/login.html`

**Step 1: 2FA 코드 입력 UI 추가**

현재 login.html에는 이미 `twofa-section` CSS 클래스와 TODO 주석이 있음. 구현:

```html
<!-- 기존 로그인 폼 -->
<div id="loginSection">
    <input type="text" id="username" placeholder="사번">
    <input type="password" id="password" placeholder="I-Net 비밀번호">
    <button onclick="handleLogin()">로그인</button>
</div>

<!-- 2FA 코드 입력 (로그인 성공 후 표시) -->
<div id="tfaSection" style="display:none;">
    <p id="tfaMessage">010-****-1234 로 인증코드를 발송했습니다</p>
    <input type="text" id="tfaCode" maxlength="6" placeholder="6자리 인증코드"
           inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code">
    <button onclick="handleVerify2fa()">인증 확인</button>
    <p id="tfaTimer">남은 시간: 5:00</p>
    <a href="#" onclick="handleResend()">코드 재발송</a>
</div>
```

**Step 2: JavaScript 플로우 수정**

```javascript
let currentCodeId = null;
let tfaTimerInterval = null;

async function handleLogin() {
    // Step 1: SSO + SMS 발송
    const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
    });
    const data = await res.json();

    if (data.code_id) {
        // 2FA 필요 → 코드 입력 UI 표시
        currentCodeId = data.code_id;
        document.getElementById('loginSection').style.display = 'none';
        document.getElementById('tfaSection').style.display = 'block';
        document.getElementById('tfaMessage').textContent =
            `${data.phone_masked} 로 인증코드를 발송했습니다`;
        startTfaTimer(300); // 5분 타이머
    } else if (data.access_token) {
        // 바이패스 로그인 (2FA 불필요) → 바로 세션 생성
        proceedWithToken(data.access_token, data);
    }
}

async function handleVerify2fa() {
    const code = document.getElementById('tfaCode').value;
    const res = await fetch(`${API_BASE}/api/v1/auth/verify-2fa`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code_id: currentCodeId, code: code })
    });

    if (!res.ok) {
        const err = await res.json();
        // 에러 표시 (코드 불일치, 만료, 잠금 등)
        showError(err.detail);
        return;
    }

    const data = await res.json();
    proceedWithToken(data.access_token, data);
}

function startTfaTimer(seconds) {
    let remaining = seconds;
    tfaTimerInterval = setInterval(() => {
        remaining--;
        const min = Math.floor(remaining / 60);
        const sec = remaining % 60;
        document.getElementById('tfaTimer').textContent =
            `남은 시간: ${min}:${String(sec).padStart(2, '0')}`;
        if (remaining <= 0) {
            clearInterval(tfaTimerInterval);
            showError('인증코드가 만료되었습니다. 다시 로그인해주세요.');
        }
    }, 1000);
}
```

**Step 3: 커밋**

```bash
git add auth-gateway/app/static/login.html
git commit -m "feat: add 2FA code input UI with timer + resend"
```

---

### Task 0-4: Admin 대시보드 로그인에도 2FA 적용

**Files:**
- Modify: `admin-dashboard/app/page.tsx` (로그인 페이지)
- Modify: `admin-dashboard/lib/api.ts`

**Step 1: API 타입 추가**

```typescript
export interface LoginStep1Response {
  code_id: string;
  phone_masked: string;
  message: string;
}

export interface Verify2faRequest {
  code_id: string;
  code: string;
}

export function verify2fa(data: Verify2faRequest): Promise<LoginResponse> {
  return request<LoginResponse>("/api/v1/auth/verify-2fa", {
    method: "POST",
    body: JSON.stringify(data),
  });
}
```

**Step 2: 로그인 페이지에 2FA 단계 추가**

기존 username/password 폼 → 성공 시 2FA 코드 입력 폼으로 전환.
Admin 사용자도 동일한 2FA 플로우를 거침.

**Step 3: 커밋**

```bash
git add admin-dashboard/app/page.tsx admin-dashboard/lib/api.ts
git commit -m "feat: add 2FA verification to admin dashboard login"
```

---

## Phase 1: P0 보안 수정 + 데이터 모델 (즉시)

### Task 1: User 모델에 security_policy JSONB 컬럼 추가

**Files:**
- Modify: `auth-gateway/app/models/user.py:8-28`
- Modify: `auth-gateway/app/schemas/user.py`

**Step 1: User 모델에 컬럼 추가**

`auth-gateway/app/models/user.py` line 28 (`updated_at` 뒤)에 추가:

```python
from sqlalchemy import JSON

# line 29 (updated_at 뒤에 추가)
security_policy = Column(JSON, nullable=True, default=None)
# None = 기본값(standard) 적용. JSONB 구조:
# {
#   "security_level": "basic|standard|full",
#   "db_access": {"safety": {"allowed": true, "tables": ["*"]}, ...},
#   "allowed_skills": ["*"] or ["report", "excel"],
#   "can_see_schema": true/false,
#   "restricted_topics": []
# }
```

**Step 2: 보안 템플릿 상수 정의**

`auth-gateway/app/schemas/security.py` 새 파일 생성:

```python
"""보안 정책 스키마 + 템플릿 상수."""
from pydantic import BaseModel

SECURITY_TEMPLATES = {
    "basic": {
        "security_level": "basic",
        "db_access": {
            "safety": {"allowed": False, "tables": []},
            "tango": {"allowed": False, "tables": []},
            "platform": {"allowed": False, "tables": []},
        },
        "allowed_skills": ["report", "share"],
        "can_see_schema": False,
        "restricted_topics": [],
    },
    "standard": {
        "security_level": "standard",
        "db_access": {
            "safety": {"allowed": True, "tables": ["*"]},
            "tango": {"allowed": True, "tables": ["*"]},
            "platform": {"allowed": False, "tables": []},
        },
        "allowed_skills": ["*"],
        "can_see_schema": True,
        "restricted_topics": [],
    },
    "full": {
        "security_level": "full",
        "db_access": {
            "safety": {"allowed": True, "tables": ["*"]},
            "tango": {"allowed": True, "tables": ["*"]},
            "platform": {"allowed": False, "tables": []},
        },
        "allowed_skills": ["*"],
        "can_see_schema": True,
        "restricted_topics": [],
    },
}

class DbAccessEntry(BaseModel):
    allowed: bool
    tables: list[str]

class SecurityPolicyData(BaseModel):
    security_level: str = "standard"
    db_access: dict[str, DbAccessEntry] = {}
    allowed_skills: list[str] = ["*"]
    can_see_schema: bool = True
    restricted_topics: list[str] = []

class SecurityPolicyUpdateRequest(BaseModel):
    security_policy: SecurityPolicyData

class SecurityPolicyResponse(BaseModel):
    user_id: int
    username: str
    security_level: str
    security_policy: dict
    pod_restart_required: bool = False

class SecurityTemplateItem(BaseModel):
    name: str
    description: str
    security_policy: dict

class SecurityTemplateListResponse(BaseModel):
    templates: list[SecurityTemplateItem]
```

**Step 3: UserResponse에 security_level 추가**

`auth-gateway/app/schemas/user.py` UserResponse 클래스에:

```python
security_level: str | None = None  # derived from security_policy
```

**Step 4: DB 마이그레이션 SQL 실행**

```bash
PGPASSWORD="BedrockPlatform2026!" psql -h aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com \
  -U bedrock_admin -d bedrock_platform -c \
  "ALTER TABLE users ADD COLUMN IF NOT EXISTS security_policy JSONB DEFAULT NULL;"
```

**Step 5: 커밋**

```bash
git add auth-gateway/app/models/user.py auth-gateway/app/schemas/security.py auth-gateway/app/schemas/user.py
git commit -m "feat: add security_policy JSONB column + templates + schemas"
```

---

### Task 2: 하드코딩된 DB 비밀번호를 K8s Secret으로 이관

**Files:**
- Modify: `auth-gateway/app/services/k8s_service.py:137`
- Modify: `container-image/entrypoint.sh:82,150`

**Step 1: K8s Secret에 TANGO 비밀번호 추가**

```bash
kubectl patch secret auth-gateway-secrets -n platform \
  --patch='{"stringData":{"TANGO_DB_PASSWORD":"TangoReadOnly2026"}}'
```

**Step 2: k8s_service.py에서 하드코딩 제거**

`k8s_service.py` line 137 변경:

```python
# Before (line 137):
# client.V1EnvVar(name="TANGO_DB_PASSWORD", value="TangoReadOnly2026"),

# After: Secret에서 로드
client.V1EnvVar(
    name="TANGO_DB_PASSWORD",
    value_from=client.V1EnvVarSource(
        secret_key_ref=client.V1SecretKeySelector(
            name="auth-gateway-secrets",
            key="TANGO_DB_PASSWORD",
        )
    ),
),
```

**Step 3: entrypoint.sh에서 하드코딩 제거**

`entrypoint.sh` line 82 변경:

```bash
# Before:
# echo "aiagentdb...TangoReadOnly2026" > /home/node/.pgpass

# After: 환경변수에서 읽기
echo "aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com:5432:postgres:claude_readonly:${TANGO_DB_PASSWORD}" > /home/node/.pgpass
chmod 600 /home/node/.pgpass

# 비밀번호 환경변수 제거 (노출 방지)
unset TANGO_DB_PASSWORD
```

`entrypoint.sh` psql-tango 스크립트도 수정 (line ~150):

```bash
cat > /home/node/.local/bin/psql-tango << 'DBSCRIPT'
#!/bin/sh
exec psql "host=aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com dbname=postgres user=claude_readonly sslmode=require" "$@"
DBSCRIPT
# .pgpass가 인증을 처리하므로 PGPASSWORD 불필요
```

**Step 4: 커밋**

```bash
git add auth-gateway/app/services/k8s_service.py container-image/entrypoint.sh
git commit -m "security: move TANGO DB password from hardcode to K8s Secret"
```

---

### Task 3: Pod securityContext + automountServiceAccountToken 추가

**Files:**
- Modify: `auth-gateway/app/services/k8s_service.py:106-113`

**Step 1: Pod spec에 보안 컨텍스트 추가**

`k8s_service.py` Pod spec 부분 (line ~106)에 추가:

```python
# Pod-level security context
security_context=client.V1PodSecurityContext(
    run_as_non_root=True,
    run_as_user=1000,
    run_as_group=1000,
    fs_group=1000,
),
automount_service_account_token=True,  # IRSA에 필요 (Bedrock 접근용)
```

Container-level security context 추가 (line ~114, container 정의 안):

```python
security_context=client.V1SecurityContext(
    allow_privilege_escalation=False,
    capabilities=client.V1Capabilities(drop=["ALL"]),
),
```

**Step 2: 커밋**

```bash
git add auth-gateway/app/services/k8s_service.py
git commit -m "security: add Pod securityContext (runAsNonRoot, drop ALL caps)"
```

---

### Task 4: CLAUDE.md + settings.json 읽기 전용 설정

**Files:**
- Modify: `container-image/Dockerfile:106-107`

**Step 1: Dockerfile에서 chmod 444 추가**

`Dockerfile` line 107 뒤에:

```dockerfile
COPY --chown=node:node config/CLAUDE.md /home/node/.claude/CLAUDE.md
COPY --chown=node:node config/settings.json /home/node/.claude/settings.json
# 보안: 사용자가 수정 불가하도록 읽기전용
RUN chmod 444 /home/node/.claude/CLAUDE.md /home/node/.claude/settings.json
```

Note: entrypoint.sh에서 CLAUDE.md를 동적 생성할 때 chmod 644로 임시 변경 후 다시 444로 설정.

**Step 2: 커밋**

```bash
git add container-image/Dockerfile
git commit -m "security: make CLAUDE.md and settings.json read-only (chmod 444)"
```

---

### Task 5: PreToolUse hook으로 위험 명령 차단

**Files:**
- Create: `container-image/config/security-hook.sh`
- Modify: `container-image/config/settings.json`
- Modify: `container-image/Dockerfile`

**Step 1: 보안 훅 스크립트 생성**

`container-image/config/security-hook.sh`:

```bash
#!/bin/bash
# PreToolUse hook: 위험 명령 실시간 차단
# exit 0 = allow, exit 2 = block
INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)

if [ "$TOOL" = "Bash" ]; then
    CMD=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)

    # 자격증명 노출 차단
    if echo "$CMD" | grep -qiE '^(env|printenv|set)$|\.pgpass|\.env|credential|password|secret|auth-gateway-secrets'; then
        echo "BLOCKED: credential exposure attempt" >&2
        exit 2
    fi

    # 외부 데이터 전송 차단
    if echo "$CMD" | grep -qiE 'curl\s+.*(-d|--data|--upload)|wget\s+.*--post|nc\s|ncat\s|socat\s'; then
        echo "BLOCKED: outbound data transfer" >&2
        exit 2
    fi

    # K8s API 접근 차단
    if echo "$CMD" | grep -qiE '/var/run/secrets|kubernetes\.default|kubectl'; then
        echo "BLOCKED: K8s API access" >&2
        exit 2
    fi
fi

exit 0
```

**Step 2: settings.json에 hooks 섹션 추가**

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "command": "/home/node/.claude/hooks/security-hook.sh",
        "timeout": 5000
      }
    ]
  },
  "env": { "CLAUDE_CODE_USE_BEDROCK": "1" },
  "theme": "dark",
  "permissions": {
    "allow": [
      "Bash(git *)", "Bash(python3 *)", "Bash(pip3 *)",
      "Bash(psql *)", "Bash(psql-tango *)", "Bash(psql-safety *)",
      "Bash(npm *)", "Bash(ls *)", "Bash(cat *)", "Bash(head *)",
      "Bash(tail *)", "Bash(wc *)", "Bash(mkdir *)", "Bash(echo *)",
      "Bash(grep *)", "Bash(find *)", "Bash(sort *)", "Bash(uvicorn *)",
      "Bash(backup-chat)", "Bash(restore-chat)",
      "Read", "Write", "Edit", "Glob", "Grep"
    ],
    "deny": [
      "Bash(curl *)", "Bash(wget *)", "Bash(nc *)", "Bash(ncat *)",
      "Bash(socat *)", "Bash(ssh *)", "Bash(scp *)"
    ]
  }
}
```

**Step 3: Dockerfile에 hook 복사 추가**

```dockerfile
COPY --chown=node:node config/security-hook.sh /home/node/.claude/hooks/security-hook.sh
RUN chmod 555 /home/node/.claude/hooks/security-hook.sh
```

**Step 4: 커밋**

```bash
git add container-image/config/security-hook.sh container-image/config/settings.json container-image/Dockerfile
git commit -m "security: add PreToolUse hook for dangerous command blocking"
```

---

## Phase 2: 보안 정책 API + CLAUDE.md 모듈화 + 조건부 Pod 설정

### Task 6: Security API 라우터 생성

**Files:**
- Create: `auth-gateway/app/routers/security.py`
- Modify: `auth-gateway/app/main.py:16,38`

**Step 1: security.py 라우터 생성**

4개 엔드포인트:
- `GET /api/v1/security/policies` - 전체 사용자 보안 정책 목록
- `GET /api/v1/security/policies/{user_id}` - 개별 사용자 정책
- `PUT /api/v1/security/policies/{user_id}` - 정책 업데이트
- `POST /api/v1/security/templates/apply/{user_id}` - 템플릿 적용
- `GET /api/v1/security/templates` - 템플릿 목록

**Step 2: main.py에 라우터 등록**

```python
from app.routers import admin, auth, sessions, users, sms, skills, telegram, security, app_proxy
# ...
app.include_router(security.router)  # app_proxy 전에
```

**Step 3: 커밋**

```bash
git add auth-gateway/app/routers/security.py auth-gateway/app/main.py
git commit -m "feat: add security policy management API (CRUD + templates)"
```

---

### Task 7: CLAUDE.md 모듈형 섹션 분리

**Files:**
- Create: `container-image/config/claude-md-sections/00-header.md`
- Create: `container-image/config/claude-md-sections/10-security-policy.md`
- Create: `container-image/config/claude-md-sections/20-tango-db.md`
- Create: `container-image/config/claude-md-sections/25-opark-db.md`
- Create: `container-image/config/claude-md-sections/30-safety-db.md`
- Create: `container-image/config/claude-md-sections/40-keyword-mapping.md`
- Create: `container-image/config/claude-md-sections/50-db-common-rules.md`
- Create: `container-image/config/claude-md-sections/60-web-terminal.md`
- Create: `container-image/config/claude-md-sections/70-webapp.md`
- Create: `container-image/config/claude-md-sections/80-telegram.md`
- Modify: `container-image/Dockerfile`

**Step 1: 현재 CLAUDE.md를 섹션별 파일로 분리**

현재 `config/CLAUDE.md`의 내용을 논리적 섹션으로 분리. 각 파일은 독립적으로 읽을 수 있어야 함.

**Step 2: Dockerfile 수정**

```dockerfile
# 정적 CLAUDE.md 대신 섹션 파일 복사
COPY --chown=node:node config/claude-md-sections/ /home/node/.claude/claude-md-sections/
# 최소 fallback CLAUDE.md (보안 정책만, DB 정보 없음)
COPY --chown=node:node config/CLAUDE.md.minimal /home/node/.claude/CLAUDE.md
RUN chmod 444 /home/node/.claude/CLAUDE.md
```

**Step 3: 커밋**

```bash
git add container-image/config/claude-md-sections/ container-image/config/CLAUDE.md.minimal container-image/Dockerfile
git commit -m "refactor: split CLAUDE.md into modular sections for per-user assembly"
```

---

### Task 8: 등급별 settings.json 분리

**Files:**
- Create: `container-image/config/settings-basic.json`
- Create: `container-image/config/settings-standard.json`
- Create: `container-image/config/settings-full.json`
- Modify: `container-image/Dockerfile`

**Step 1: basic용 settings.json 생성**

```json
{
  "hooks": {
    "PreToolUse": [{"matcher": "Bash", "command": "/home/node/.claude/hooks/security-hook.sh", "timeout": 5000}]
  },
  "permissions": {
    "allow": [
      "Bash(git *)", "Bash(python3 *)", "Bash(pip3 *)", "Bash(npm *)",
      "Bash(ls *)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)",
      "Bash(wc *)", "Bash(mkdir *)", "Bash(echo *)", "Bash(grep *)",
      "Bash(find *)", "Bash(sort *)", "Bash(uvicorn *)",
      "Bash(backup-chat)", "Bash(restore-chat)",
      "Read", "Write", "Edit", "Glob", "Grep"
    ],
    "deny": [
      "Bash(psql *)", "Bash(psql-tango *)", "Bash(psql-safety *)",
      "Bash(curl *)", "Bash(wget *)", "Bash(nc *)", "Bash(ncat *)",
      "Bash(ssh *)", "Bash(scp *)", "Bash(socat *)"
    ]
  }
}
```

standard와 full은 psql 허용 + deny에서 psql 제거.

**Step 2: Dockerfile에 모두 복사**

```dockerfile
COPY --chown=node:node config/settings-basic.json /home/node/.claude/settings-basic.json
COPY --chown=node:node config/settings-standard.json /home/node/.claude/settings-standard.json
COPY --chown=node:node config/settings-full.json /home/node/.claude/settings-full.json
```

**Step 3: 커밋**

```bash
git add container-image/config/settings-*.json container-image/Dockerfile
git commit -m "feat: per-security-level settings.json with differentiated deny lists"
```

---

### Task 9: k8s_service.py에 보안 정책 주입

**Files:**
- Modify: `auth-gateway/app/services/k8s_service.py:50-57,119-145`

**Step 1: create_pod 시그니처에 security_policy 추가**

```python
def create_pod(
    self, username: str, session_type: str = "workshop",
    user_display_name: str = "", ttl_seconds: int = 14400,
    target_node: str | None = None,
    security_policy: dict | None = None,  # NEW
) -> str:
```

**Step 2: 보안 정책에 따라 환경변수 조건부 주입**

```python
import json

policy = security_policy or SECURITY_TEMPLATES["standard"]
security_level = policy.get("security_level", "standard")
db_access = policy.get("db_access", {})

env_vars = [
    # 항상 주입
    V1EnvVar(name="SECURITY_LEVEL", value=security_level),
    V1EnvVar(name="SECURITY_POLICY", value=json.dumps(policy)),
    V1EnvVar(name="CLAUDE_CODE_USE_BEDROCK", value="1"),
    V1EnvVar(name="AWS_REGION", value=self.settings.bedrock_region),
    # ... 기존 non-DB 환경변수 ...
]

# DB 자격증명: 허용된 DB만 주입
if db_access.get("safety", {}).get("allowed", False):
    env_vars.append(V1EnvVar(name="DATABASE_URL", value=self.settings.workshop_database_url))

if db_access.get("tango", {}).get("allowed", False):
    env_vars.append(V1EnvVar(name="TANGO_DB_PASSWORD",
        value_from=V1EnvVarSource(secret_key_ref=V1SecretKeySelector(
            name="auth-gateway-secrets", key="TANGO_DB_PASSWORD"))))
    env_vars.append(V1EnvVar(name="TANGO_DATABASE_URL", value=self.settings.tango_database_url))
```

**Step 3: sessions.py에서 security_policy 전달**

```python
# sessions.py create_session() 안에서:
from app.schemas.security import SECURITY_TEMPLATES
policy = user.security_policy if user.security_policy else SECURITY_TEMPLATES["standard"]
pod_name = k8s.create_pod(username, user_pod_ttl, user_display_name,
    ttl_seconds=ttl_seconds, security_policy=policy)
```

**Step 4: 커밋**

```bash
git add auth-gateway/app/services/k8s_service.py auth-gateway/app/routers/sessions.py
git commit -m "feat: conditional env var injection based on user security_policy"
```

---

### Task 10: entrypoint.sh 동적 보안 설정 생성

**Files:**
- Modify: `container-image/entrypoint.sh`

**Step 1: 보안 정책 파싱 섹션 추가**

`.pgpass` 생성 전에 삽입:

```bash
# ---------------------------------------------------------------------------
# 3) 보안 정책 처리 (SECURITY_LEVEL, SECURITY_POLICY 환경변수 기반)
# ---------------------------------------------------------------------------
SECURITY_LEVEL="${SECURITY_LEVEL:-standard}"
echo "  보안 등급: ${SECURITY_LEVEL}"

# settings.json 선택 (등급별)
cp "/home/node/.claude/settings-${SECURITY_LEVEL}.json" /home/node/.claude/settings.json 2>/dev/null
chmod 444 /home/node/.claude/settings.json
```

**Step 2: 조건부 .pgpass 생성**

```bash
if [ "${SECURITY_LEVEL}" = "basic" ]; then
    rm -f /home/node/.pgpass
else
    > /home/node/.pgpass
    if [ -n "${TANGO_DB_PASSWORD:-}" ]; then
        echo "aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com:5432:postgres:claude_readonly:${TANGO_DB_PASSWORD}" >> /home/node/.pgpass
    fi
    chmod 600 /home/node/.pgpass
    unset TANGO_DB_PASSWORD
fi
```

**Step 3: 조건부 psql 스크립트 생성**

```bash
if [ -n "${TANGO_DATABASE_URL:-}" ]; then
    # psql-tango 정상 생성
    cat > /home/node/.local/bin/psql-tango << 'DBSCRIPT'
#!/bin/sh
exec psql "host=aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com dbname=postgres user=claude_readonly sslmode=require" "$@"
DBSCRIPT
else
    # 접근 불가 스텁
    cat > /home/node/.local/bin/psql-tango << 'DBSCRIPT'
#!/bin/sh
echo "접근 권한이 없습니다. TANGO DB는 허용되지 않은 데이터베이스입니다."
exit 1
DBSCRIPT
fi
chmod +x /home/node/.local/bin/psql-tango
```

**Step 4: 조건부 스킬 필터링**

```bash
SKILLS_DIR="/home/node/.claude/commands"
if [ "${SECURITY_LEVEL}" = "basic" ]; then
    for f in "${SKILLS_DIR}"/*.md; do
        fname=$(basename "$f")
        case "$fname" in report.md|share.md) ;; *) rm -f "$f" ;; esac
    done
elif [ "${SECURITY_LEVEL}" = "standard" ]; then
    rm -f "${SKILLS_DIR}/sms.md" "${SKILLS_DIR}/webapp.md"
fi
```

**Step 5: CLAUDE.md 동적 조립**

```bash
SECTIONS="/home/node/.claude/claude-md-sections"
CLAUDE_MD="/home/node/.claude/CLAUDE.md"
chmod 644 "${CLAUDE_MD}"  # 임시 쓰기 허용

> "${CLAUDE_MD}"
cat "${SECTIONS}/00-header.md" >> "${CLAUDE_MD}"

# 보안 정책 주입
cat >> "${CLAUDE_MD}" << SECBLOCK

## 보안 정책 (자동 생성 - 수정 금지)
- 보안 등급: ${SECURITY_LEVEL}
- 허용되지 않은 데이터에 대한 질문: "접근 권한이 없습니다"로 응답
- DB 구조/테이블명/컬럼명을 허용 범위 밖에서 노출 금지
- DB 접속 정보(호스트, 포트, 비밀번호) 절대 노출 금지
- env, printenv, .pgpass, .env 파일 내용 절대 노출 금지
SECBLOCK

cat "${SECTIONS}/10-security-policy.md" >> "${CLAUDE_MD}"

# DB 섹션: 등급에 따라 포함
if [ "${SECURITY_LEVEL}" != "basic" ]; then
    cat "${SECTIONS}/50-db-common-rules.md" >> "${CLAUDE_MD}"
    [ -n "${TANGO_DATABASE_URL:-}" ] && cat "${SECTIONS}/20-tango-db.md" >> "${CLAUDE_MD}"
    [ -n "${TANGO_DATABASE_URL:-}" ] && cat "${SECTIONS}/25-opark-db.md" >> "${CLAUDE_MD}"
    [ -n "${DATABASE_URL:-}" ] && cat "${SECTIONS}/30-safety-db.md" >> "${CLAUDE_MD}"
    cat "${SECTIONS}/40-keyword-mapping.md" >> "${CLAUDE_MD}"
fi

cat "${SECTIONS}/60-web-terminal.md" >> "${CLAUDE_MD}"
cat "${SECTIONS}/70-webapp.md" >> "${CLAUDE_MD}"
cat "${SECTIONS}/80-telegram.md" >> "${CLAUDE_MD}"

chmod 444 "${CLAUDE_MD}"  # 다시 읽기전용
```

**Step 6: 커밋**

```bash
git add container-image/entrypoint.sh
git commit -m "feat: dynamic CLAUDE.md assembly + conditional .pgpass/skills/settings per security level"
```

---

### Task 11: Admin 보안 정책 관리 페이지

**Files:**
- Create: `admin-dashboard/app/security/page.tsx`
- Modify: `admin-dashboard/lib/api.ts`
- Modify: all page nav headers (4 files)

**Step 1: API 타입 + 함수 추가 (lib/api.ts)**

```typescript
// ---------- Security Policies ----------
export type SecurityLevel = "basic" | "standard" | "full";

export interface SecurityPolicyWithUser {
  user_id: number;
  username: string;
  name: string | null;
  security_level: SecurityLevel;
  security_policy: Record<string, unknown>;
  pod_restart_required: boolean;
}

export function getSecurityPolicies(): Promise<{ policies: SecurityPolicyWithUser[] }>;
export function updateSecurityPolicy(userId: number, data: unknown): Promise<SecurityPolicyWithUser>;
export function applySecurityTemplate(userId: number, template: SecurityLevel): Promise<SecurityPolicyWithUser>;
export function getSecurityTemplates(): Promise<{ templates: unknown[] }>;
```

**Step 2: /security 페이지 생성**

사용자 목록 + 보안 등급 뱃지 + 인라인 편집:
- 보안 등급 라디오 (basic/standard/full)
- DB 접근 토글 (Safety, Tango)
- 스킬 체크박스 (6개)
- 스키마 노출 토글
- 제한 주제 태그 입력
- 저장/초기화/템플릿 적용 버튼

**Step 3: 네비게이션 업데이트 (5개 페이지)**

모든 페이지 nav에 "보안 정책" 링크 추가:

```
운용현황 | 사용자 관리 | 보안 정책 | 토큰 사용량 | 인프라
```

**Step 4: 커밋**

```bash
git add admin-dashboard/app/security/page.tsx admin-dashboard/lib/api.ts \
  admin-dashboard/app/dashboard/page.tsx admin-dashboard/app/users/page.tsx \
  admin-dashboard/app/usage/page.tsx admin-dashboard/app/infra/page.tsx
git commit -m "feat: admin security policy management page with per-user controls"
```

---

## Phase 3: PostgreSQL 역할 분리

### Task 12: PostgreSQL 등급별 역할 생성

**Files:**
- Create: `infra/sql/postgresql-access-control.sql`

**Step 1: Safety DB에 역할 생성**

```sql
-- Standard: 대부분 테이블 접근, 인사정보 제외
CREATE USER claude_safety_ro WITH PASSWORD '<generated>' CONNECTION LIMIT 20;
GRANT CONNECT ON DATABASE safety TO claude_safety_ro;
GRANT USAGE ON SCHEMA public TO claude_safety_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO claude_safety_ro;
REVOKE SELECT ON auth_user, accounts_userprofile, accounts_passwordhistory,
    django_session, django_admin_log FROM claude_safety_ro;

-- Full: 블랙리스트만 제외
CREATE USER claude_safety_full_ro WITH PASSWORD '<generated>' CONNECTION LIMIT 5;
GRANT CONNECT ON DATABASE safety TO claude_safety_full_ro;
GRANT USAGE ON SCHEMA public TO claude_safety_full_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO claude_safety_full_ro;
REVOKE SELECT ON accounts_passwordhistory, django_session FROM claude_safety_full_ro;
```

**Step 2: TANGO DB에 역할 생성**

```sql
-- Standard: 알람 + Opark 테이블만
CREATE USER claude_tango_ro WITH PASSWORD '<generated>' CONNECTION LIMIT 20;
GRANT CONNECT ON DATABASE postgres TO claude_tango_ro;
GRANT USAGE ON SCHEMA public TO claude_tango_ro;
GRANT SELECT ON alarm_data, alarm_events, alarm_statistics, alarm_history,
    alarm_hourly_summary, facility_info, opark_daily_report TO claude_tango_ro;
```

**Step 3: K8s Secret에 새 비밀번호 추가**

```bash
kubectl patch secret auth-gateway-secrets -n platform --patch='{"stringData":{
    "SAFETY_DB_PW_STANDARD":"<password>",
    "SAFETY_DB_PW_FULL":"<password>",
    "TANGO_DB_PW_STANDARD":"<password>"
}}'
```

**Step 4: 커밋**

```bash
git add infra/sql/postgresql-access-control.sql
git commit -m "security: create per-level PostgreSQL roles with table-level GRANT/REVOKE"
```

---

## Phase 4: 빌드 + 배포 + 검증

### Task 13: 전체 빌드 + 배포

**Step 1: Auth Gateway 빌드 + 배포**

```bash
cd auth-gateway
docker build --no-cache --platform linux/amd64 -t auth-gateway .
docker tag auth-gateway:latest 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest
docker push 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest
kubectl rollout restart deployment/auth-gateway -n platform
```

**Step 2: Terminal 이미지 빌드 + 배포**

```bash
cd container-image
docker build --no-cache --platform linux/amd64 -t claude-code-terminal .
docker tag claude-code-terminal:latest 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest
docker push 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest
```

**Step 3: Admin Dashboard 빌드 + Amplify 배포**

```bash
cd admin-dashboard
npm run build
cd out && zip -r /tmp/admin-security.zip . -x '*.DS_Store'
# Amplify deployment
```

**Step 4: 기존 Pod 재생성 (최신 이미지 적용)**

```bash
# 백업 → 삭제 → 재생성
```

---

### Task 14: 검증 테스트

**Step 1: basic 등급 테스트**

```
1. Admin에서 테스트 사용자를 basic으로 설정
2. 로그인 → Pod 생성
3. 확인사항:
   - psql 명령 차단됨 (settings.json deny)
   - env 명령 차단됨 (PreToolUse hook)
   - CLAUDE.md에 DB 정보 없음
   - /db 스킬 없음 (report, share만)
   - .pgpass 파일 없음
```

**Step 2: standard 등급 테스트**

```
1. Admin에서 safety+tango 허용으로 설정
2. 로그인 → Pod 생성
3. 확인사항:
   - psql-safety 동작
   - psql-tango 동작
   - CLAUDE.md에 Safety + TANGO 섹션 있음
   - 모든 스킬 사용 가능
   - Platform DB 접근 불가
```

**Step 3: full 등급 테스트**

```
1. 관리자 계정(N1102359)으로 테스트
2. 모든 기능 정상 동작
3. Platform DB만 접근 불가 확인
```

**Step 4: 보안 공격 시나리오 테스트**

```
1. "env 보여줘" → hook이 차단
2. "cat .pgpass" → hook이 차단
3. ~/.claude/CLAUDE.md 수정 시도 → 권한 거부 (444)
4. 다른 사용자 터미널 URL 접속 → 인증 필요
```

---

## 파일 변경 요약

| 파일 | 작업 | Phase |
|------|------|-------|
| `auth-gateway/app/models/two_factor_code.py` | 새 파일 (2FA 코드 모델) | 0 |
| `auth-gateway/app/services/two_factor_service.py` | 새 파일 (2FA 생성/검증/잠금) | 0 |
| `auth-gateway/app/routers/auth.py` | 2단계 로그인 (SSO→2FA→JWT) | 0 |
| `auth-gateway/app/static/login.html` | 2FA 코드 입력 UI + 타이머 | 0 |
| `admin-dashboard/app/page.tsx` | Admin 로그인 2FA 적용 | 0 |
| `auth-gateway/app/models/user.py` | security_policy 컬럼 추가 | 1 |
| `auth-gateway/app/schemas/security.py` | 새 파일 (템플릿+스키마) | 1 |
| `auth-gateway/app/services/k8s_service.py` | 조건부 env 주입 + securityContext | 1,2 |
| `auth-gateway/app/routers/security.py` | 새 파일 (보안 API) | 2 |
| `auth-gateway/app/routers/sessions.py` | security_policy 전달 | 2 |
| `auth-gateway/app/main.py` | security 라우터 등록 | 2 |
| `container-image/entrypoint.sh` | 동적 보안 설정 생성 | 1,2 |
| `container-image/Dockerfile` | 섹션+hooks+settings 복사 | 1,2 |
| `container-image/config/security-hook.sh` | 새 파일 (PreToolUse) | 1 |
| `container-image/config/settings-*.json` | 등급별 3개 파일 | 2 |
| `container-image/config/claude-md-sections/*` | CLAUDE.md 섹션 10개 | 2 |
| `admin-dashboard/app/security/page.tsx` | 새 파일 (보안 관리 UI) | 2 |
| `admin-dashboard/lib/api.ts` | 보안 API 타입+함수 | 2 |
| `infra/sql/postgresql-access-control.sql` | PG 역할 SQL | 3 |
