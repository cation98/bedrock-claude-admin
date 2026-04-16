# 플랫폼 SMS 발송 권한 모델 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `User.can_send_sms` Boolean 플래그를 도입하고 `POST /api/v1/sms/send` 에 권한 pre-check를 추가하여, 정병오(N1001063)님만 SMS를 발송할 수 있게 한다. 동시에 sms.py를 Pod 인증(`X-Pod-Name`/`X-Pod-Token`)까지 수용하도록 확장하고, 정병오 Pod의 `sms_worker.py` 헤더를 교체하여 실제 Pod → auth-gateway 경로가 동작하도록 한다.

**Architecture:** auth-gateway 레이어에 단일 Boolean 컬럼 + pre-check를 추가하고, 인증 dependency를 기존 `get_current_user_or_pod`(이미 구현됨)로 교체한다. Pod 측은 EFS에 있는 `sms_worker.py` 파일의 헤더 2줄만 교체하면 되므로 이미지 재빌드 불필요. DAILY_LIMIT 기반 일일 한도 검사 블록은 `send_sms()` 본문에서 제거한다(권한 보유자 무제한).

**Tech Stack:** FastAPI / SQLAlchemy / Alembic / pytest / Python 3.12 / PostgreSQL (prod) + SQLite (tests)

**Spec 참조:** `docs/superpowers/specs/2026-04-16-platform-sms-permission-design.md`

---

## File Structure

| File | 유형 | 역할 |
|------|------|------|
| `auth-gateway/app/models/user.py` | Modify | `User` 모델에 `can_send_sms` 컬럼 추가 |
| `auth-gateway/alembic/versions/f6a7b8c9d0e1_add_can_send_sms.py` | Create | 컬럼 추가 + N1001063 seed 마이그레이션 |
| `auth-gateway/app/routers/sms.py` | Modify | dependency 교체(`get_current_user_or_pod`) + pre-check + DAILY_LIMIT 블록 제거 |
| `auth-gateway/tests/conftest.py` | Modify | `create_test_user` 에 `can_send_sms` 파라미터 + `mock_sms_gateway` 픽스처 + `override_current_user` 헬퍼 추가 |
| `auth-gateway/tests/test_sms_permission.py` | Create | 권한 pre-check 4케이스 (403 / 200 JWT / 200 Pod / unlimited) |
| `/efs/users/n1001063/sms_worker.py` (정병오 Pod 내 `~/workspace/sms_worker.py`) | Modify | `Authorization: Bearer ${AUTH_TOKEN}` → `X-Pod-Name` + `X-Pod-Token: ${SECURE_POD_TOKEN}` |

---

## Task 1: User 모델에 `can_send_sms` 컬럼 추가

**Files:**
- Modify: `auth-gateway/app/models/user.py` (end of `User` class body, 자연스러운 위치는 기존 `can_deploy_custom_auth` 정의 아래)

- [ ] **Step 1.1: 파일 읽기로 현재 상태 확인**

Run: `sed -n '1,40p' auth-gateway/app/models/user.py`
Expected: `class User(Base):` 와 `can_deploy_custom_auth = ...` 컬럼이 보여야 함.

- [ ] **Step 1.2: 컬럼 추가**

`auth-gateway/app/models/user.py` 의 `can_deploy_custom_auth` 정의 바로 아래에 다음 2줄을 추가한다:

```python
    # SMS 발송 권한 — admin이 개별 부여. 권한 보유자는 일일 한도 없이 발송 가능.
    can_send_sms = Column(Boolean, default=False, nullable=False, server_default='false')
```

- [ ] **Step 1.3: 구문 오류 없음 확인**

Run: `cd auth-gateway && python -c "from app.models.user import User; print([c.name for c in User.__table__.columns if 'sms' in c.name or 'deploy' in c.name])"`
Expected: `['can_deploy_apps', 'can_deploy_custom_auth', 'can_send_sms']` 가 출력.

- [ ] **Step 1.4: Commit**

```bash
git add auth-gateway/app/models/user.py
git commit -m "feat(user): add can_send_sms column (default false)"
```

---

## Task 2: Alembic 마이그레이션 작성

**Files:**
- Create: `auth-gateway/alembic/versions/f6a7b8c9d0e1_add_can_send_sms.py`

- [ ] **Step 2.1: 마이그레이션 파일 생성**

`auth-gateway/alembic/versions/f6a7b8c9d0e1_add_can_send_sms.py` 를 다음 내용으로 생성:

```python
"""Add can_send_sms to users + seed N1001063

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-16
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "can_send_sms",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    # 1차 릴리스 정책: 정병오(N1001063)님만 초기 허용
    op.execute("UPDATE users SET can_send_sms = true WHERE username = 'N1001063'")


def downgrade() -> None:
    op.drop_column("users", "can_send_sms")
```

- [ ] **Step 2.2: alembic head 중복 없음 확인**

Run: `cd auth-gateway && python -c "import sqlalchemy; from alembic.script import ScriptDirectory; from alembic.config import Config; sd = ScriptDirectory.from_config(Config('alembic.ini')); print('heads:', sd.get_heads())"`
Expected: `heads: ('f6a7b8c9d0e1',)` (새 revision이 유일한 head).

- [ ] **Step 2.3: SQLite에서 upgrade 시뮬레이션 (단위 검증)**

Run:
```bash
cd auth-gateway && python - <<'PY'
import sqlalchemy as sa
from alembic.config import Config
from alembic import command

eng = sa.create_engine("sqlite:///:memory:")
cfg = Config("alembic.ini")
cfg.set_main_option("sqlalchemy.url", "sqlite:///:memory:")
cfg.attributes["connection"] = eng.connect()
# 주: 실제 upgrade는 초기 테이블 부재로 오류 가능 — 대신 새 마이그레이션 파일이 로드되는지만 확인
from alembic.script import ScriptDirectory
sd = ScriptDirectory.from_config(cfg)
rev = sd.get_revision("f6a7b8c9d0e1")
print("revision loaded:", rev.revision, "down:", rev.down_revision)
PY
```
Expected: `revision loaded: f6a7b8c9d0e1 down: e5f6a7b8c9d0`.

- [ ] **Step 2.4: Commit**

```bash
git add auth-gateway/alembic/versions/f6a7b8c9d0e1_add_can_send_sms.py
git commit -m "feat(alembic): add can_send_sms column + seed N1001063"
```

---

## Task 3: 테스트 인프라 확장 (conftest)

**Files:**
- Modify: `auth-gateway/tests/conftest.py` (Helper fixtures 섹션 안)

- [ ] **Step 3.1: `create_test_user` 에 `can_send_sms` 파라미터 추가**

`auth-gateway/tests/conftest.py` 의 `create_test_user` 팩토리에서, 내부 `_create` 함수 시그니처와 User 생성 구문을 다음과 같이 수정한다 (기존 `can_deploy_apps` 아래에 새 파라미터 추가):

```python
    def _create(
        username: str = "TESTUSER01",
        name: str = "Test User",
        role: str = "user",
        is_approved: bool = True,
        can_deploy_apps: bool = True,
        can_send_sms: bool = True,  # 테스트 기본값 True — 회귀 방지
    ) -> User:
        user = User(
            username=username,
            name=name,
            role=role,
            is_approved=is_approved,
            can_deploy_apps=can_deploy_apps,
            can_send_sms=can_send_sms,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user
```

- [ ] **Step 3.2: `override_current_user` 헬퍼 + `mock_sms_gateway` 픽스처 추가**

`conftest.py` 파일 끝에 다음 두 블록을 추가:

```python
@pytest.fixture()
def override_current_user(monkeypatch):
    """테스트 내에서 _mock_current_user가 반환하는 dict를 바꾸는 헬퍼.

    Usage:
        override_current_user(username="TESTUSER", auth_type="jwt")
    """
    from app.core import security as sec_module

    def _set(username: str = "TESTUSER01", auth_type: str = "jwt"):
        def _mock():
            return {
                "sub": username,
                "username": username,
                "role": "user",
                "auth_type": auth_type,
            }
        _test_app.dependency_overrides[sec_module.get_current_user] = _mock
        _test_app.dependency_overrides[sec_module.get_current_user_or_pod] = _mock

    return _set


@pytest.fixture()
def mock_sms_gateway(monkeypatch):
    """외부 SMS 게이트웨이 호출을 차단하고 고정 응답 반환."""
    class _Resp:
        status_code = 200
        def json(self):
            return {"Result": "00", "ResultMsg": "OK"}

    async def _fake_post(self, *args, **kwargs):
        return _Resp()

    monkeypatch.setattr("httpx.AsyncClient.post", _fake_post)
    yield
```

- [ ] **Step 3.3: import 확인**

Run: `cd auth-gateway && python -c "from tests.conftest import create_test_user, override_current_user, mock_sms_gateway"`
Expected: ImportError 없음 (fixture import만 테스트).

- [ ] **Step 3.4: 기존 테스트 회귀 없음 확인**

Run: `cd auth-gateway && python -m pytest tests/ -x -q --tb=line 2>&1 | tail -20`
Expected: 기존 테스트들이 PASS 유지 (새 컬럼 기본값 True로 회귀 없음).

- [ ] **Step 3.5: Commit**

```bash
git add auth-gateway/tests/conftest.py
git commit -m "test(conftest): add can_send_sms fixture + mock_sms_gateway helper"
```

---

## Task 4: 권한 없음 테스트 (RED) — `can_send_sms=False` → 403

**Files:**
- Create: `auth-gateway/tests/test_sms_permission.py`

- [ ] **Step 4.1: 실패할 테스트 작성**

`auth-gateway/tests/test_sms_permission.py` 를 다음 내용으로 생성:

```python
"""SMS 발송 권한 pre-check 테스트.

User.can_send_sms=False 인 사용자는 POST /api/v1/sms/send 호출 시 403.
권한 보유자는 일일 한도 없이 발송 가능해야 한다.
"""


def test_send_sms_403_when_no_permission(
    client, db_session, create_test_user, override_current_user
):
    create_test_user(username="NOPERM01", can_send_sms=False)
    override_current_user(username="NOPERM01")

    resp = client.post(
        "/api/v1/sms/send",
        json={"phone_number": "010-0000-0000", "message": "hi"},
    )
    assert resp.status_code == 403, resp.text
    assert "권한이 없습니다" in resp.json()["detail"]
```

- [ ] **Step 4.2: 테스트가 실패하는지 확인 (RED)**

Run: `cd auth-gateway && python -m pytest tests/test_sms_permission.py::test_send_sms_403_when_no_permission -xvs 2>&1 | tail -20`
Expected: FAIL — 현재 sms.py는 권한 체크가 없으므로 403이 아닌 다른 응답(200 또는 422 또는 502). 이 단계에서는 실패해야 정상.

---

## Task 5: 권한 pre-check 구현 (GREEN)

**Files:**
- Modify: `auth-gateway/app/routers/sms.py`

- [ ] **Step 5.1: User import 추가**

`auth-gateway/app/routers/sms.py` 상단 import 섹션에 추가:

```python
from app.models.user import User
```

- [ ] **Step 5.2: `send_sms()` 에 pre-check 삽입**

기존 `send_sms()` 함수 본문 첫 줄(`username = current_user["sub"]` 직후)에 다음 블록 삽입:

```python
    # --- 신설: 권한 pre-check ---
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.can_send_sms:
        raise HTTPException(
            status_code=403,
            detail="SMS 발송 권한이 없습니다. 관리자에게 문의하세요.",
        )
```

- [ ] **Step 5.3: 테스트가 통과하는지 확인 (GREEN)**

Run: `cd auth-gateway && python -m pytest tests/test_sms_permission.py::test_send_sms_403_when_no_permission -xvs 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 5.4: Commit**

```bash
git add auth-gateway/app/routers/sms.py auth-gateway/tests/test_sms_permission.py
git commit -m "feat(sms): 403 when can_send_sms=false"
```

---

## Task 6: 권한 보유자 200 테스트 + 구현 검증

**Files:**
- Modify: `auth-gateway/tests/test_sms_permission.py`

- [ ] **Step 6.1: 테스트 추가**

`tests/test_sms_permission.py` 파일 끝에 추가:

```python
def test_send_sms_ok_when_permission_granted(
    client, db_session, create_test_user, override_current_user, mock_sms_gateway
):
    create_test_user(username="OK01", can_send_sms=True)
    override_current_user(username="OK01")

    resp = client.post(
        "/api/v1/sms/send",
        json={"phone_number": "010-0000-0000", "message": "hi"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
```

- [ ] **Step 6.2: 테스트 실행**

Run: `cd auth-gateway && python -m pytest tests/test_sms_permission.py::test_send_sms_ok_when_permission_granted -xvs 2>&1 | tail -15`
Expected: PASS (권한 보유자는 pre-check 통과 → 외부 gateway 모킹 응답 200).

- [ ] **Step 6.3: Commit**

```bash
git add auth-gateway/tests/test_sms_permission.py
git commit -m "test(sms): 200 when can_send_sms=true"
```

---

## Task 7: DAILY_LIMIT 블록 제거 + 무제한 테스트

**Files:**
- Modify: `auth-gateway/app/routers/sms.py`
- Modify: `auth-gateway/tests/test_sms_permission.py`

- [ ] **Step 7.1: 무제한 테스트 작성 (RED)**

`tests/test_sms_permission.py` 끝에 추가:

```python
def test_send_sms_unlimited_for_permitted_user(
    client, db_session, create_test_user, override_current_user, mock_sms_gateway
):
    create_test_user(username="OK01", can_send_sms=True)
    override_current_user(username="OK01")

    for i in range(11):
        resp = client.post(
            "/api/v1/sms/send",
            json={"phone_number": "010-0000-0000", "message": f"msg {i}"},
        )
        assert resp.status_code == 200, f"iteration {i}: {resp.text}"
```

- [ ] **Step 7.2: 테스트 실패 확인 (RED)**

Run: `cd auth-gateway && python -m pytest tests/test_sms_permission.py::test_send_sms_unlimited_for_permitted_user -xvs 2>&1 | tail -15`
Expected: FAIL at iteration 10 with HTTP 429 (기존 DAILY_LIMIT=10 블록이 아직 존재하므로 11번째에서 거부).

- [ ] **Step 7.3: `send_sms()` 에서 DAILY_LIMIT 검사 블록 제거**

`auth-gateway/app/routers/sms.py` 의 `send_sms()` 함수 본문에서 다음 블록을 삭제:

```python
    # 일일 발송 한도 확인
    today_count = _get_today_count(db, username)
    if today_count >= DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"일일 발송 한도 초과 ({DAILY_LIMIT}건/일)",
        )
```

같은 함수 아래쪽에서 `today_count` 를 참조하는 라인들(예: `remaining = DAILY_LIMIT - today_count - 1` 같은 logging 라인)도 제거한다. `_get_today_count` 헬퍼 함수 자체는 `/usage` 엔드포인트에서 사용하므로 **삭제하지 않는다**.

`DAILY_LIMIT = 10` 모듈 상수 선언 바로 위에 주석 추가:

```python
# 현재는 사용되지 않음 — 향후 권한-한도 분리 정책 도입 시 재사용 예정
DAILY_LIMIT = 10
```

- [ ] **Step 7.4: 테스트 통과 확인 (GREEN)**

Run: `cd auth-gateway && python -m pytest tests/test_sms_permission.py -xvs 2>&1 | tail -20`
Expected: 3개 테스트 모두 PASS.

- [ ] **Step 7.5: Commit**

```bash
git add auth-gateway/app/routers/sms.py auth-gateway/tests/test_sms_permission.py
git commit -m "refactor(sms): drop DAILY_LIMIT gate (permitted users are unlimited)"
```

---

## Task 8: Pod 인증 dependency 교체

**Files:**
- Modify: `auth-gateway/app/routers/sms.py`
- Modify: `auth-gateway/tests/test_sms_permission.py`

- [ ] **Step 8.1: Pod 인증 테스트 작성 (RED)**

`tests/test_sms_permission.py` 끝에 추가:

```python
def test_send_sms_ok_via_pod_auth(
    client, db_session, create_test_user, override_current_user, mock_sms_gateway
):
    """Pod 경로(X-Pod-Name/X-Pod-Token) 로 들어와도 권한 체크가 동작해야 함."""
    create_test_user(username="N1001063", can_send_sms=True)
    override_current_user(username="N1001063", auth_type="pod")

    resp = client.post(
        "/api/v1/sms/send",
        json={"phone_number": "010-0000-0000", "message": "from pod"},
    )
    assert resp.status_code == 200, resp.text
```

- [ ] **Step 8.2: 테스트 결과 확인**

Run: `cd auth-gateway && python -m pytest tests/test_sms_permission.py::test_send_sms_ok_via_pod_auth -xvs 2>&1 | tail -10`
Expected: 결과 판단 — conftest의 `override_current_user` 가 `get_current_user` 와 `get_current_user_or_pod` 둘 다 override 하므로 이 테스트는 이미 PASS 가능. 실제 Pod 헤더 파싱 테스트는 integration 단에서 확인.

PASS 시: 추가 변경 없이 Step 8.3 건너뛰고 8.4로.
FAIL 시: 8.3 진행.

- [ ] **Step 8.3: `send_sms()` 및 `get_sms_usage()` dependency 교체**

`auth-gateway/app/routers/sms.py` 수정:

```python
# import 변경
from app.core.security import get_current_user_or_pod  # 기존 get_current_user 대신 (또는 병행 import)

# send_sms() 파라미터 변경
@router.post("/send", response_model=SmsSendResponse)
async def send_sms(
    request: SmsSendRequest,
    current_user: dict = Depends(get_current_user_or_pod),  # ← 교체
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    ...

# get_sms_usage() 파라미터 변경
@router.get("/usage")
async def get_sms_usage(
    current_user: dict = Depends(get_current_user_or_pod),  # ← 교체
    db: Session = Depends(get_db),
):
    ...
```

`username = current_user["sub"]` 는 이미 `sub` 키를 쓰고 있으므로 수정 불필요.

- [ ] **Step 8.4: 전체 테스트 통과 확인**

Run: `cd auth-gateway && python -m pytest tests/test_sms_permission.py tests/test_sms*.py -xvs 2>&1 | tail -20`
Expected: 4개 테스트 모두 PASS. 기존 `test_sms*.py` 가 있다면 함께 PASS 유지.

- [ ] **Step 8.5: Commit**

```bash
git add auth-gateway/app/routers/sms.py auth-gateway/tests/test_sms_permission.py
git commit -m "feat(sms): accept pod auth (X-Pod-Name/Token) via get_current_user_or_pod"
```

---

## Task 9: 전체 테스트 회귀 확인

- [ ] **Step 9.1: auth-gateway 전체 pytest 실행**

Run: `cd auth-gateway && python -m pytest tests/ -q --tb=line 2>&1 | tail -30`
Expected: 모든 테스트 PASS. 실패 시 회귀 내용 확인 후 수정.

- [ ] **Step 9.2: lint 확인 (있다면)**

Run: `cd auth-gateway && python -m ruff check app/routers/sms.py app/models/user.py 2>&1 || echo "ruff not installed — skip"`
Expected: 오류 없음 또는 ruff 미설치 스킵.

- [ ] **Step 9.3: 서버 구동 smoke test**

Run: `cd auth-gateway && python -c "from app.main import app; print([r.path for r in app.routes if '/sms' in str(getattr(r, 'path', ''))])"`
Expected: `['/api/v1/sms/send', '/api/v1/sms/usage']` 두 엔드포인트가 정상 등록되어 있음.

---

## Task 10: 정병오 Pod `sms_worker.py` 헤더 교체

**Files:**
- Modify: 정병오 Pod 내 `/home/node/workspace/sms_worker.py` (≡ EFS `/efs/users/n1001063/sms_worker.py`)

- [ ] **Step 10.1: 원본 백업**

Run: `kubectl exec claude-terminal-n1001063 -n claude-sessions -- cp /home/node/workspace/sms_worker.py /home/node/workspace/sms_worker.py.bak`
Expected: (no output).

- [ ] **Step 10.2: 신규 sms_worker.py 를 로컬 임시 파일로 작성**

Write tool (또는 `cat > /tmp/sms_worker.py <<'PY' ... PY` 형태의 heredoc) 로 `/tmp/sms_worker.py` 파일을 다음 내용 그대로 저장:

```python
#!/usr/bin/env python3
"""SMS 발송 워커 — Pod 인증 헤더 방식 (X-Pod-Name/X-Pod-Token)"""
import os, json, time, urllib.request, urllib.error

QUEUE_DIR = os.path.expanduser("~/workspace/sms_queue")
SMS_API_URL = "http://auth-gateway.platform:8000/api/v1/sms/send"
os.makedirs(QUEUE_DIR, exist_ok=True)


def _headers():
    return {
        "Content-Type": "application/json",
        "X-Pod-Name": os.environ.get("HOSTNAME", ""),
        "X-Pod-Token": os.environ.get("SECURE_POD_TOKEN", ""),
    }


def send_one(filepath):
    with open(filepath) as f:
        task = json.load(f)
    phone = task["phone"]
    message = task["message"]
    body = json.dumps({"phone_number": phone, "message": message}).encode()
    req = urllib.request.Request(SMS_API_URL, data=body, headers=_headers())
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = {"ok": True, "resp": json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        result = {"ok": False, "error": f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}"}
    except Exception as e:
        result = {"ok": False, "error": str(e)}

    out = filepath.replace(".json", ".result.json")
    with open(out, "w") as f:
        json.dump(result, f)
    os.remove(filepath)
    print(f"[sms_worker] {phone} -> {result}", flush=True)


def _check_api_reachable():
    try:
        req = urllib.request.Request(SMS_API_URL, method="GET")
        urllib.request.urlopen(req, timeout=5)
        print(f"[sms_worker] API 연결 확인 OK: {SMS_API_URL}", flush=True)
    except urllib.error.HTTPError as e:
        print(f"[sms_worker] API 연결 확인 OK (HTTP {e.code}): {SMS_API_URL}", flush=True)
    except Exception as e:
        print(f"[sms_worker] !! API 연결 실패: {SMS_API_URL} -- {e}", flush=True)


if __name__ == "__main__":
    print("[sms_worker] 시작, 큐 감시 중:", QUEUE_DIR, flush=True)
    _check_api_reachable()
    while True:
        for fname in sorted(os.listdir(QUEUE_DIR)):
            if fname.endswith(".json") and not fname.endswith(".result.json"):
                send_one(os.path.join(QUEUE_DIR, fname))
        time.sleep(1)
```

검증 명령: `python3 -c "import ast; ast.parse(open('/tmp/sms_worker.py').read()); print('syntax OK')"`
Expected: `syntax OK`.

- [ ] **Step 10.3: Pod로 파일 복사**

Run: `kubectl cp /tmp/sms_worker.py claude-sessions/claude-terminal-n1001063:/home/node/workspace/sms_worker.py`
Expected: 에러 없음. (만약 컨테이너에 `tar` 가 없어 kubectl cp 실패하면, `kubectl exec ... -- bash -c 'cat > /home/node/workspace/sms_worker.py <<EOF ... EOF'` 로 대체.)

- [ ] **Step 10.4: 복사 검증**

Run: `kubectl exec claude-terminal-n1001063 -n claude-sessions -- grep -c "X-Pod-Token" /home/node/workspace/sms_worker.py`
Expected: `1` (헤더 문자열 1회 등장).

- [ ] **Step 10.5: 실행 중 워커 프로세스 재시작**

Run:
```bash
kubectl exec claude-terminal-n1001063 -n claude-sessions -- bash -c '
pkill -f "sms_worker.py" 2>/dev/null || true
sleep 1
nohup python3 /home/node/workspace/sms_worker.py >/home/node/workspace/sms_worker.log 2>&1 &
sleep 2
head -5 /home/node/workspace/sms_worker.log
'
```
Expected: `[sms_worker] 시작` 및 `API 연결 확인 OK` 메시지 출력.

> 정병오님이 `signature_app` 을 재시작하는 경우 lifespan 핸들러가 워커를 재시작하므로 이 단계를 자동화 가능.

---

## Task 11: 배포 및 통합 검증

- [ ] **Step 11.1: PR 생성**

Run:
```bash
cd /Users/cation98/Projects/bedrock-ai-agent && \
  git log --oneline main~5..HEAD && \
  echo "--- above commits will go in PR ---"
```
Expected: Task 1~9 커밋이 보임. 이 브랜치를 push하고 PR 생성:

```bash
git push origin HEAD:feature/sms-permission-gate
gh pr create --title "feat(sms): can_send_sms 권한 게이트 + Pod 인증 수용" \
  --body "$(cat <<'BODY'
## Summary
- User.can_send_sms 컬럼 + Alembic 마이그레이션 (N1001063 seed)
- sms.py send_sms/usage에 get_current_user_or_pod dependency 교체
- 권한 pre-check 추가, DAILY_LIMIT 블록 제거 (권한자 무제한)
- Pod 내 sms_worker.py 헤더 교체 (별도 EFS 편집, 이 PR과 동기 배포)

## Test plan
- [x] pytest tests/test_sms_permission.py 4개 전부 PASS
- [x] pytest tests/ 전체 회귀 없음
- [ ] staging 배포 후 Pod curl 검증 (아래)
BODY
)"
```

- [ ] **Step 11.2: CI 및 머지**

CI green 확인 후 main으로 머지. Alembic upgrade가 파이프라인에서 자동 실행됨.

- [ ] **Step 11.3: auth-gateway Pod 롤링 재시작 완료 확인**

Run: `kubectl get pods -n platform -l app=auth-gateway -o custom-columns='NAME:.metadata.name,AGE:.metadata.creationTimestamp'`
Expected: 두 Pod 모두 최근 생성 시각(배포 직후).

- [ ] **Step 11.4: DB 컬럼 적용 확인**

Run:
```bash
kubectl exec -n platform deploy/auth-gateway -- python -c "
from app.core.database import get_db
from app.models.user import User
from sqlalchemy import select
db = next(get_db())
rows = db.execute(select(User.username, User.can_send_sms).where(User.can_send_sms == True)).all()
print('can_send_sms=True users:', rows)
"
```
Expected: `[('N1001063', True)]` (정병오님만).

- [ ] **Step 11.5: Pod 경로 E2E 검증**

Run:
```bash
kubectl exec claude-terminal-n1001063 -n claude-sessions -- bash -c '
curl -sS -X POST http://auth-gateway.platform:8000/api/v1/sms/send \
  -H "Content-Type: application/json" \
  -H "X-Pod-Name: $HOSTNAME" \
  -H "X-Pod-Token: $SECURE_POD_TOKEN" \
  -d "{\"phone_number\":\"010-0000-0000\",\"message\":\"deploy verification\"}"
echo
'
```
Expected: `{"success":true,"message":"010-0000-0000로 SMS 발송 완료", ...}` (실제 SMS 발송). 실제 전화번호는 정병오님과 협의하여 설정.

- [ ] **Step 11.6: 권한 없는 사용자 거절 확인**

Run:
```bash
# 활성 상태인 다른 사용자 Pod 선택 (예: claude-terminal-n1001064)
kubectl exec claude-terminal-n1001064 -n claude-sessions -- bash -c '
curl -sS -o /dev/null -w "%{http_code}\n" -X POST http://auth-gateway.platform:8000/api/v1/sms/send \
  -H "Content-Type: application/json" \
  -H "X-Pod-Name: $HOSTNAME" \
  -H "X-Pod-Token: $SECURE_POD_TOKEN" \
  -d "{\"phone_number\":\"010-0000-0000\",\"message\":\"should be 403\"}"
'
```
Expected: `403`.

- [ ] **Step 11.7: signature_app E2E 확인**

정병오님 화면에서 signature_app의 "📱 SMS 전송" 버튼을 눌러 실제 SMS가 도달하는지 확인.
Expected: SMS 수신 성공, `~/workspace/sms_queue/*.result.json` 에 `{"ok": true, ...}` 기록.

- [ ] **Step 11.8: 완료 보고 커밋**

```bash
cd /Users/cation98/Projects/bedrock-ai-agent && \
  git log --oneline main~10..HEAD -- auth-gateway/app/routers/sms.py auth-gateway/alembic/versions/f6a7b8c9d0e1_add_can_send_sms.py
```

---

## Rollback 절차

- auth-gateway 이미지를 이전 태그로 되돌림 (컬럼은 drop하지 않아도 이전 코드 동작에 영향 없음)
- 필요 시 Alembic downgrade: `kubectl exec -n platform deploy/auth-gateway -- alembic downgrade -1`
- `sms_worker.py` 는 Pod 내에서 `mv sms_worker.py.bak sms_worker.py` 로 원본 복구

---

## Post-deploy 주의사항

- `DAILY_LIMIT` 상수는 코드에 남아 있음 — 차후 권한-한도 분리 정책 도입 시 재사용
- 추가 사용자 허용 시: `kubectl exec -n platform deploy/auth-gateway -- python -c "from app.core.database import get_db; from app.models.user import User; db = next(get_db()); db.query(User).filter(User.username == 'NXXXXXXX').update({'can_send_sms': True}); db.commit()"` (admin UI 도입 전 임시 방식)
- admin UI/API 는 다음 이터레이션에서 설계 (spec Section 6 참조)
