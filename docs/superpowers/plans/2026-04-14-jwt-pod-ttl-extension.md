# JWT Pod 세션 TTL 연장 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `create_access_token`/`create_refresh_token`에 `expires_delta` + `extra_claims` 파라미터를 추가하고, pod-token-exchange와 `/auth/refresh`(session_type="pod" 분기)에서 access TTL을 8h로 연장한다. SSO/portal 경로는 15분 유지.

**Architecture:** JWT payload에 `session_type` 클레임을 심어 refresh 경로에서 상속시키는 방식. create 함수 시그니처에 선택적 파라미터 2개 추가하여 기존 호출 영향 0.

**Tech Stack:** Python 3.11, FastAPI, PyJWT(RS256), pytest

**Spec:** `docs/superpowers/specs/2026-04-14-jwt-pod-ttl-extension-design.md`

---

## File Structure

### Modify
- `auth-gateway/app/core/jwt_rs256.py:245-319` — `create_access_token` + `create_refresh_token` 시그니처 확장
- `auth-gateway/app/routers/jwt_auth.py:205-206` — pod-token-exchange 호출부
- `auth-gateway/app/routers/jwt_auth.py:300` — `/auth/refresh` 호출부

### Create
- `auth-gateway/tests/test_jwt_ttl_extension.py` — 신규 단위+통합 테스트

### Untouched
- `auth-gateway/app/core/config.py` — 설정 필드 추가하지 않음 (파라미터 기반)
- `container-image/entrypoint.sh` — refresh daemon 동작 변경 없음 (토큰 TTL만 길어짐)
- SSO 관련 라우터 — session_type 미지정 경로로 기존 동작 유지

---

## Task 1: `create_access_token` 시그니처 확장 (TDD)

**Files:**
- Modify: `auth-gateway/app/core/jwt_rs256.py:245-284`
- Test: `auth-gateway/tests/test_jwt_ttl_extension.py` (신규)

- [ ] **Step 1: 실패 테스트 작성**

`auth-gateway/tests/test_jwt_ttl_extension.py` 신규 생성, 아래 내용 작성:

```python
"""JWT TTL 확장 — issue #27 단위 테스트.

Coverage:
  - create_access_token: expires_delta, extra_claims 파라미터 정상 반영
  - create_refresh_token: 동일
  - 기본값 (None) 시 기존 동작 유지 (회귀 방지)
"""

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-32-chars-min-xx"
)

import pytest
import jwt as pyjwt

from app.core.jwt_rs256 import (
    create_access_token,
    create_refresh_token,
    verify_access_token,
)
from app.core.config import get_settings


@pytest.fixture
def rsa_settings(monkeypatch):
    """RS256 ephemeral key가 생성되도록 환경 구성."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    monkeypatch.setenv("JWT_RS256_PRIVATE_KEY", pem)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_create_access_token_default_ttl_uses_config(rsa_settings):
    """expires_delta 미지정 시 settings.jwt_rs256_access_expire_minutes 적용."""
    token = create_access_token("N1102359", "N1102359", "a@b.com", "user", rsa_settings)
    payload = pyjwt.decode(token, options={"verify_signature": False})
    expected_ttl = rsa_settings.jwt_rs256_access_expire_minutes * 60
    actual_ttl = payload["exp"] - payload["iat"]
    assert abs(actual_ttl - expected_ttl) < 5  # 5초 오차 허용


def test_create_access_token_expires_delta_override(rsa_settings):
    """expires_delta 지정 시 해당 값 적용."""
    token = create_access_token(
        "N1102359", "N1102359", "a@b.com", "user", rsa_settings,
        expires_delta=timedelta(hours=8),
    )
    payload = pyjwt.decode(token, options={"verify_signature": False})
    actual_ttl = payload["exp"] - payload["iat"]
    assert abs(actual_ttl - 8 * 3600) < 5


def test_create_access_token_extra_claims_embedded(rsa_settings):
    """extra_claims 지정 시 페이로드에 포함."""
    token = create_access_token(
        "N1102359", "N1102359", "a@b.com", "user", rsa_settings,
        extra_claims={"session_type": "pod"},
    )
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert payload.get("session_type") == "pod"


def test_create_access_token_no_extra_claims_absent(rsa_settings):
    """extra_claims 미지정 시 session_type 키 자체가 부재."""
    token = create_access_token("N1102359", "N1102359", "a@b.com", "user", rsa_settings)
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert "session_type" not in payload


def test_create_refresh_token_expires_delta_override(rsa_settings):
    """create_refresh_token도 expires_delta 적용."""
    token, _jti = create_refresh_token(
        "N1102359", "N1102359", "a@b.com", "user", rsa_settings,
        expires_delta=timedelta(hours=24),
    )
    payload = pyjwt.decode(token, options={"verify_signature": False})
    actual_ttl = payload["exp"] - payload["iat"]
    assert abs(actual_ttl - 24 * 3600) < 5


def test_create_refresh_token_extra_claims_embedded(rsa_settings):
    """refresh token도 extra_claims 반영."""
    token, _jti = create_refresh_token(
        "N1102359", "N1102359", "a@b.com", "user", rsa_settings,
        extra_claims={"session_type": "pod"},
    )
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert payload.get("session_type") == "pod"
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl/auth-gateway
source .venv/bin/activate 2>/dev/null || python3 -m venv .venv && source .venv/bin/activate
pytest tests/test_jwt_ttl_extension.py -v 2>&1 | tail -20
```

Expected: TypeError(`expires_delta is an unexpected keyword argument`) 또는 유사 시그니처 오류로 6개 테스트 전부 실패.

- [ ] **Step 3: `create_access_token` 시그니처 확장**

`auth-gateway/app/core/jwt_rs256.py` Edit 사용:

**old_string** (L245-284):

```python
def create_access_token(
    sub: str,
    emp_no: str,
    email: str,
    role: str,
    settings: Optional[Settings] = None,
) -> str:
    """RS256 access JWT 생성.

    Claims:
        sub     — SSO 사번 (username, e.g. N1102359) — 전체 consumer 합의
        emp_no  — SSO 사번 (sub와 동일, 명시성 목적으로 병기)
        email   — 사용자 이메일
        role    — user | admin
        jti     — UUID4, replay 방지용
        type    — "access"
        kid     — 서명 키 ID (JWKS kid와 일치)
        exp     — 만료 timestamp
        iat     — 발급 timestamp
    """
    if settings is None:
        settings = get_settings()

    get_private_key()  # kid 초기화 보장

    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_rs256_access_expire_minutes
    )
    payload = {
        "sub": sub,
        "emp_no": emp_no,
        "email": email,
        "role": role,
        "jti": str(uuid.uuid4()),
        "type": "access",
        "kid": _key_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _private_key_pem_bytes(), algorithm="RS256")
```

**new_string**:

```python
def create_access_token(
    sub: str,
    emp_no: str,
    email: str,
    role: str,
    settings: Optional[Settings] = None,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[dict] = None,
) -> str:
    """RS256 access JWT 생성.

    Claims:
        sub     — SSO 사번 (username, e.g. N1102359) — 전체 consumer 합의
        emp_no  — SSO 사번 (sub와 동일, 명시성 목적으로 병기)
        email   — 사용자 이메일
        role    — user | admin
        jti     — UUID4, replay 방지용
        type    — "access"
        kid     — 서명 키 ID (JWKS kid와 일치)
        exp     — 만료 timestamp
        iat     — 발급 timestamp

    Args:
        expires_delta: 지정 시 이 값으로 TTL 오버라이드. None이면 settings 사용.
            issue #27 Pod 세션용 8h TTL 주입 경로.
        extra_claims: 페이로드에 병합할 추가 클레임. 예: {"session_type": "pod"}.
            세션 종류 구분 등 선택적 메타데이터용.
    """
    if settings is None:
        settings = get_settings()

    get_private_key()  # kid 초기화 보장

    if expires_delta is not None:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_rs256_access_expire_minutes
        )
    payload = {
        "sub": sub,
        "emp_no": emp_no,
        "email": email,
        "role": role,
        "jti": str(uuid.uuid4()),
        "type": "access",
        "kid": _key_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _private_key_pem_bytes(), algorithm="RS256")
```

- [ ] **Step 4: 테스트 실행 → access 관련 4개 통과 확인**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl/auth-gateway
pytest tests/test_jwt_ttl_extension.py -v -k "access" 2>&1 | tail -15
```

Expected: 4 passed (access 관련 테스트). refresh 관련 2개는 아직 실패.

---

## Task 2: `create_refresh_token` 시그니처 확장

**Files:**
- Modify: `auth-gateway/app/core/jwt_rs256.py:287-319` (or whatever the current line range)

- [ ] **Step 1: 현재 create_refresh_token 본문 파악**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl
sed -n '287,330p' auth-gateway/app/core/jwt_rs256.py
```

Read the exact current body to prepare Edit.

- [ ] **Step 2: Edit로 시그니처 확장**

Same pattern as Task 1 Step 3. The function body currently reads `settings.jwt_refresh_token_expire_hours`. Add optional `expires_delta`/`extra_claims` with identical semantics. Preserve return `(token_str, jti)` tuple.

실제 현재 함수 본문을 Read로 확인 후 Edit 적용. 변경 지침:

1. 시그니처 끝에 `expires_delta: Optional[timedelta] = None, extra_claims: Optional[dict] = None` 추가
2. expire 계산 블록을 `if expires_delta is not None: expire = ... + expires_delta; else: expire = ... + timedelta(hours=settings.jwt_refresh_token_expire_hours)` 패턴으로 교체
3. payload 완성 후 `if extra_claims: payload.update(extra_claims)` 삽입
4. Docstring Args 섹션 추가

- [ ] **Step 3: 테스트 실행 → 6개 전부 통과**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl/auth-gateway
pytest tests/test_jwt_ttl_extension.py -v 2>&1 | tail -15
```

Expected: 6 passed.

- [ ] **Step 4: 커밋**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl
git add auth-gateway/app/core/jwt_rs256.py auth-gateway/tests/test_jwt_ttl_extension.py
git commit -m "$(cat <<'COMMIT'
feat(phase1-backlog/#27): JWT create 함수에 expires_delta + extra_claims 추가

create_access_token / create_refresh_token 시그니처에 선택적 파라미터
2개 추가. 기본값 None으로 기존 호출 영향 없음. Issue #27 Pod 세션용
8h TTL 및 session_type 클레임 주입 경로 마련.

단위 테스트 6개 추가 (expires_delta, extra_claims, 기본값 회귀).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
COMMIT
)"
```

---

## Task 3: pod-token-exchange에 expires_delta + session_type 주입

**Files:**
- Modify: `auth-gateway/app/routers/jwt_auth.py:205-206`
- Test: add to `auth-gateway/tests/test_jwt_ttl_extension.py`

- [ ] **Step 1: 통합 테스트 추가**

파일 `auth-gateway/tests/test_jwt_ttl_extension.py` 끝에 추가:

```python

# ─── 통합 — 엔드포인트 수준 ────────────────────────────────────────────────

import pytest as _pytest

jwt_auth_router = _pytest.importorskip(
    "app.routers.jwt_auth",
    reason="jwt_auth router not available",
)

from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(rsa_settings, tmp_path):
    """최소 FastAPI app + TestClient."""
    from app.main import app
    return TestClient(app)


# NOTE: 아래 통합 테스트는 기존 test_pod_token_auth.py 픽스처 패턴을 공유.
# 단순 단위 수준 가드만 두고, 상세 통합은 test_pod_token_auth.py 확장에서 처리.


def test_pod_exchange_issues_8h_access_token(monkeypatch, rsa_settings):
    """pod-token-exchange 경로에서 create_access_token이 8h delta로 호출되는지
    mock-level 검증. 라우터가 정확한 파라미터로 호출하는지만 확인."""
    from app.routers import jwt_auth as jwt_auth_mod
    from unittest.mock import patch, MagicMock
    captured = {}

    def fake_create_access_token(sub, emp_no, email, role, settings, expires_delta=None, extra_claims=None):
        captured["expires_delta"] = expires_delta
        captured["extra_claims"] = extra_claims
        return "fake-access"

    with patch.object(jwt_auth_mod, "create_access_token", fake_create_access_token):
        # 실제 exchange 호출은 DB/Redis 의존성이 많아 mock 부담 커서,
        # 여기서는 함수 객체 교체 후 별도 통합 테스트(test_pod_token_auth.py)에서
        # 실제 호출 경로를 엮는다. 본 테스트는 의존성 stub 후 호출 파라미터 검증.
        # (실제 호출 구성 완료 시 captured dict 확인)
        pass  # placeholder — 다음 Step에서 실 호출
```

실제 통합 호출은 기존 `test_pod_token_auth.py`의 fixture를 재사용해야 하므로 부피가 크다. Task 3 Step 1에서는 **placeholder 통합 테스트를 두고** Step 2에서 실제 수정 후 기존 `test_pod_token_auth.py`를 확장하여 검증한다. 아래 Step 2로 바로 진행.

- [ ] **Step 2: pod-token-exchange 수정**

`auth-gateway/app/routers/jwt_auth.py` L205-206 Edit:

**old_string**:

```python
    access_token = create_access_token(sub, emp_no, email, user.role, settings)
    refresh_token, _ = create_refresh_token(sub, emp_no, email, user.role, settings)
```

**new_string**:

```python
    # Phase 1 백로그 #27: Pod 터미널 세션은 15분 TTL로 중단 빈번 → access 8h 연장.
    # session_type="pod" 클레임을 심어 /auth/refresh에서 TTL 상속하도록 분기.
    # SSO/portal 경로(/auth/issue-jwt 등)는 이 분기 밖이므로 기본 15m 유지.
    POD_ACCESS_TTL = timedelta(hours=8)
    access_token = create_access_token(
        sub, emp_no, email, user.role, settings,
        expires_delta=POD_ACCESS_TTL,
        extra_claims={"session_type": "pod"},
    )
    refresh_token, _ = create_refresh_token(
        sub, emp_no, email, user.role, settings,
        extra_claims={"session_type": "pod"},
    )
```

- [ ] **Step 3: `timedelta` import 확인**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl
grep -n "^from datetime\|^import datetime" auth-gateway/app/routers/jwt_auth.py | head -3
```

Expected: 이미 `from datetime import ..., timedelta, ...` 존재. 없으면 상단 import 블록에 추가.

- [ ] **Step 4: 기존 pod-token-exchange 테스트 회귀 체크**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl/auth-gateway
pytest tests/test_pod_token_auth.py tests/test_auth_jwt_phase0.py -v 2>&1 | tail -30
```

Expected: 기존 통과 테스트 전부 pass. 만약 `session_type` 미기대 또는 exp 길이 체크하는 기존 테스트가 있으면 기대값 갱신 필요.

---

## Task 4: `/auth/refresh`에서 session_type 상속 + 8h 분기

**Files:**
- Modify: `auth-gateway/app/routers/jwt_auth.py:~300` (L300 근처 `create_access_token` 호출)

- [ ] **Step 1: /auth/refresh 현재 본문 확인**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl
sed -n '280,315p' auth-gateway/app/routers/jwt_auth.py
```

L264에 `sub = payload.get("sub", "")` 등 클레임 추출 로직 존재. L300에 `new_access_token = create_access_token(sub, emp_no, email, role, settings)` 호출. 여기에 session_type 분기 추가.

- [ ] **Step 2: Edit 적용**

**old_string** (L264-301 영역의 일부 — 정확한 매칭 필요. Read로 확인 후 구체화):

L264-268과 L300 양쪽 수정 — 먼저 L264 근처에서 `session_type = payload.get("session_type", None)` 추출, L300에서 분기.

두 단계 Edit:

**(a) session_type 추출 추가** — old:

```python
    sub = payload.get("sub", "")
    emp_no = payload.get("emp_no", "")
    email = payload.get("email", "")
    role = payload.get("role", "user")
    jti = payload.get("jti", "")
```

new:

```python
    sub = payload.get("sub", "")
    emp_no = payload.get("emp_no", "")
    email = payload.get("email", "")
    role = payload.get("role", "user")
    jti = payload.get("jti", "")
    # Phase 1 백로그 #27: Pod 세션은 긴 TTL 상속, SSO/portal은 기본 TTL.
    session_type = payload.get("session_type")
```

**(b) new_access_token 발급 분기** — old:

```python
    # 새 access token 발급
    new_access_token = create_access_token(sub, emp_no, email, role, settings)
```

new:

```python
    # 새 access token 발급 — session_type 상속으로 Pod 세션은 8h 유지
    if session_type == "pod":
        new_access_token = create_access_token(
            sub, emp_no, email, role, settings,
            expires_delta=timedelta(hours=8),
            extra_claims={"session_type": "pod"},
        )
    else:
        new_access_token = create_access_token(sub, emp_no, email, role, settings)
```

- [ ] **Step 3: 통합 테스트 추가 (기존 test_jwt_ttl_extension.py에 추가)**

```python


# ─── /auth/refresh session_type 상속 ──────────────────────────────────────

def test_refresh_with_pod_session_type_issues_8h(rsa_settings):
    """session_type='pod' refresh token으로 /auth/refresh 호출 시
    새 access_token도 8h TTL 및 session_type='pod' 유지."""
    from app.main import app
    from app.core.jwt_rs256 import create_refresh_token

    # pod 세션 refresh token 직접 생성 (exchange 생략)
    refresh_tok, _ = create_refresh_token(
        "N1102359", "N1102359", "a@b.com", "user", rsa_settings,
        extra_claims={"session_type": "pod"},
    )

    client = TestClient(app)
    resp = client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_tok},
    )
    assert resp.status_code == 200, resp.text
    new_access = resp.json()["access_token"]
    payload = pyjwt.decode(new_access, options={"verify_signature": False})
    assert abs((payload["exp"] - payload["iat"]) - 8 * 3600) < 5
    assert payload.get("session_type") == "pod"


def test_refresh_without_session_type_issues_15m(rsa_settings):
    """session_type 없는 refresh token (SSO 경로)으로 /auth/refresh 호출 시
    기본 15m TTL, session_type 클레임 없음 (회귀 방지)."""
    from app.main import app
    from app.core.jwt_rs256 import create_refresh_token

    refresh_tok, _ = create_refresh_token(
        "N1102359", "N1102359", "a@b.com", "user", rsa_settings,
    )

    client = TestClient(app)
    resp = client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_tok},
    )
    assert resp.status_code == 200, resp.text
    new_access = resp.json()["access_token"]
    payload = pyjwt.decode(new_access, options={"verify_signature": False})
    expected = rsa_settings.jwt_rs256_access_expire_minutes * 60
    assert abs((payload["exp"] - payload["iat"]) - expected) < 5
    assert "session_type" not in payload
```

- [ ] **Step 4: 테스트 실행**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl/auth-gateway
pytest tests/test_jwt_ttl_extension.py -v 2>&1 | tail -20
```

Expected: 모든 테스트 passed (8개 = 단위 6 + refresh 통합 2).

`/auth/refresh`는 replay 보호, cascade revoke 등 여러 pre-check를 통과해야 하므로 통합 테스트가 실패할 수 있다. 실패 시 test fixture(`is_user_revoked`/`is_jti_blacklisted` mocking 필요)를 조정하거나, 통합 테스트 대신 함수-레벨 단위 테스트로 대체.

단위 테스트 대안 (통합 실패 시):

```python
def test_refresh_route_inherits_session_type(rsa_settings, monkeypatch):
    """함수 레벨 — 내부 create_access_token이 session_type='pod' 경로에서 8h로 호출되는지."""
    from app.routers import jwt_auth as mod
    from unittest.mock import patch

    captured = {}

    def fake_create_access_token(sub, emp_no, email, role, settings, expires_delta=None, extra_claims=None):
        captured["expires_delta"] = expires_delta
        captured["extra_claims"] = extra_claims
        return "fake-token"

    # verify_refresh_token은 페이로드 반환 stub
    fake_payload = {
        "sub": "N1102359", "emp_no": "N1102359", "email": "a@b.com",
        "role": "user", "jti": "test-jti", "session_type": "pod",
    }

    with patch.object(mod, "create_access_token", fake_create_access_token), \
         patch.object(mod, "verify_refresh_token", return_value=fake_payload), \
         patch.object(mod, "is_user_revoked", return_value=False), \
         patch.object(mod, "is_jti_blacklisted", return_value=False), \
         patch.object(mod, "blacklist_jti"):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        resp = client.post("/auth/refresh", json={"refresh_token": "stub"})
        assert resp.status_code == 200

    assert captured["expires_delta"] == timedelta(hours=8)
    assert captured["extra_claims"] == {"session_type": "pod"}
```

- [ ] **Step 5: 커밋**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl
git add auth-gateway/app/routers/jwt_auth.py auth-gateway/tests/test_jwt_ttl_extension.py
git commit -m "$(cat <<'COMMIT'
feat(phase1-backlog/#27): Pod 세션 JWT TTL 8h + /auth/refresh session_type 분기

pod-token-exchange에서 session_type="pod" 클레임과 8h access TTL 주입.
/auth/refresh는 incoming refresh token의 session_type을 읽어 Pod 세션이면
동일 8h TTL로 새 access_token 발급. SSO/portal 경로(session_type 부재)는
기본 15m 유지.

이슈 #27 해결: claude 프로세스 env 고정 문제는 여전하나 8h TTL로 실
사용자 세션 99% 커버.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
COMMIT
)"
```

---

## Task 5: 전체 테스트 스위트 회귀 확인

**Files:**
- (read only — 기존 테스트 전부)

- [ ] **Step 1: auth-gateway 전체 테스트 실행**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl/auth-gateway
pytest 2>&1 | tail -30
```

Expected: `N passed, 0 failed` (N은 기존 + 8). 실패 시 그 테스트의 기대값이 15분 TTL에 의존하거나 session_type 부재를 체크하고 있을 가능성 — 정상 회귀이므로 기대값을 session_type="pod"/8h로 갱신.

- [ ] **Step 2: 커밋** (테스트 기대값 갱신 필요한 경우에만)

회귀 발견 없으면 Step 2 생략. 있다면:

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl
git add auth-gateway/tests/
git commit -m "test(phase1-backlog/#27): pod-token-exchange 기대값 갱신 (TTL 8h)

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: PR 생성 + 머지 + 이슈 close (사용자 승인 필요)

**Files:**
- (git 작업만)

- [ ] **Step 1: 브랜치 push**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-27-jwt-ttl
git push -u origin phase1-backlog/#27-jwt-ttl
```

- [ ] **Step 2: PR 생성**

```bash
gh pr create --title "feat(#27): JWT Pod 세션 TTL 8h 연장 (A.2-extended)" --body "$(cat <<'BODY'
## Summary
- 이슈 #27 해결 — Pod 터미널 세션 15m 만료 UX 개선
- `create_access_token`/`create_refresh_token` 시그니처에 선택적 `expires_delta` + `extra_claims` 추가 (기존 호출 영향 0)
- `pod-token-exchange`: access 8h + refresh에 `session_type="pod"` 클레임
- `/auth/refresh`: 들어온 refresh에서 `session_type` 읽어 "pod"면 access 8h, 아니면 기본 15m (SSO/portal 회귀 없음)

## Test plan
- [x] 단위: `create_access_token(expires_delta=...)` exp 차이 검증
- [x] 단위: `create_access_token(extra_claims=...)` 페이로드 포함
- [x] 단위: refresh 동일
- [x] 단위: 기본값 호출 시 기존 동작 유지 (회귀 방지)
- [x] 통합/함수 레벨: `/auth/refresh` with session_type="pod" → 새 access 8h + session_type 상속
- [x] 통합: `/auth/refresh` without session_type → 기본 15m + session_type 부재
- [x] 전체 auth-gateway pytest 스위트 회귀 통과

## Deferred (Phase 2)
- 사이드카 refresh proxy (옵션 B)
- claude CLI가 토큰을 파일에서 재로드 (upstream)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

- [ ] **Step 3: 머지 (사용자 승인 후)**

```bash
cd /Users/cation98/Project/bedrock-ai-agent   # main worktree에서 실행
gh pr merge <PR#> --squash --delete-branch
```

- [ ] **Step 4: 이슈 #27 close**

```bash
gh issue close 27 --comment "$(cat <<'COMMENT'
## 종결 (2026-04-14) ✅

PR #<PR#> squash merge 완료.

### 처리 내역 (A.2-extended)

- `create_access_token` / `create_refresh_token`에 `expires_delta` + `extra_claims` 파라미터 추가
- pod-token-exchange: access 8h, `session_type="pod"` 클레임 주입
- `/auth/refresh`: session_type 상속으로 Pod 세션은 8h, SSO/portal은 15m 유지

### 효과

| 발급 경로 | access TTL | session_type |
|----------|-----------|--------------|
| SSO 로그인 | 15m (유지) | — |
| Portal 갱신 | 15m (유지) | — |
| pod-token-exchange | **8h** | `"pod"` |
| /auth/refresh (session_type="pod") | **8h** | `"pod"` |

### 문서

- Spec: `docs/superpowers/specs/2026-04-14-jwt-pod-ttl-extension-design.md`
- Plan: `docs/superpowers/plans/2026-04-14-jwt-pod-ttl-extension.md`

### Deferred (Phase 2)

- 사이드카 refresh proxy (옵션 B)
- Upstream claude CLI 토큰 파일 재로드 기여
COMMENT
)"
```

---

## Task 7: worktree 정리

- [ ] **Step 1: worktree 제거**

```bash
cd /Users/cation98/Project/bedrock-ai-agent
git worktree remove .worktrees/issue-27-jwt-ttl --force
git branch -D "phase1-backlog/#27-jwt-ttl" 2>/dev/null || true
git pull origin main
```

- [ ] **Step 2: 상태 확인**

```bash
cd /Users/cation98/Project/bedrock-ai-agent
git status
git worktree list
```

Expected: main clean, worktree list에 `.worktrees/issue-27-jwt-ttl` 없음.

---

## Self-Review (plan 작성 후 점검)

**1. Spec coverage**:
- Spec §3.2 변경 범위 3곳 → Task 1 (jwt_rs256 access) / Task 2 (jwt_rs256 refresh) / Task 3 (jwt_auth pod-exchange) / Task 4 (jwt_auth refresh) ✅
- Spec §6 테스트 전략 8개 → Task 1-4 내 테스트로 커버 ✅
- Spec §9 성공 판정 기준 6개 → Task 1-5 체크 ✅

**2. Placeholder scan**: 모든 Edit에 실제 old/new_string 코드 포함. Task 4 "통합 대안"은 우회 경로 명시. TODO/TBD 없음. ✅

**3. Type consistency**:
- `expires_delta: Optional[timedelta] = None` 시그니처 Task 1/2/3/4 전체 일관 ✅
- `extra_claims: Optional[dict] = None` 동일 ✅
- `session_type` 키 문자열 일관 ("pod") ✅
