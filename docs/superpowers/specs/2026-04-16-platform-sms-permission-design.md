# 플랫폼 SMS 발송 권한 모델 (Minimal 1차 릴리스)

- 작성일: 2026-04-16
- 작성자: Claude Code 브레인스토밍 세션 (`jungbyeongoh-pod-app-mod`)
- 연관 이슈 맥락: 정병오님(N1001063) signature_app.py 내 SMS 발송 기능을 플랫폼 공식 허용으로 격상

## 1. 배경과 목적

### 1.1 현 상태

- `auth-gateway/app/routers/sms.py` 의 `POST /api/v1/sms/send` 엔드포인트는 이미 구현·배포되어 있으나 **JWT 인증만 지원** (`Depends(get_current_user)`)
- JWT 인증만 통과하면 **모든 사용자**가 호출 가능 (단, 일일 10건 한도)
- 정병오님의 `signature_app.py` → `~/workspace/sms_worker.py` 는 `Authorization: Bearer ${AUTH_TOKEN}` 헤더로 호출하도록 작성되어 있으나, **정병오 Pod에는 `AUTH_TOKEN` 환경변수가 주입되어 있지 않음** (`kubectl exec claude-terminal-n1001063 -- env` 로 확인). 결과적으로 현재 SMS 요청은 빈 Bearer 토큰으로 송신되어 실제로는 **401로 실패하고 있는 상태**.
- Pod이 보유한 인증 자격증명은 `SECURE_POD_TOKEN` (헤더 쌍 `X-Pod-Name` + `X-Pod-Token` 용). auth-gateway 측에는 이미 이를 수용하는 `get_current_user_or_pod` dependency가 `app/core/security.py:136` 에 구현돼 있으나 sms.py는 이를 사용하지 않음.
- 정리: "기능은 구현돼 있으나 Pod ↔ auth-gateway 인증이 연결되지 않아 **실제로는 동작하지 않는 상태**"이며, 이 문서의 목표는 (1) 권한 게이트 추가 + (2) 이 연결을 성립시키는 것.

### 1.2 목표

즉시 달성할 목표(이 문서의 범위):

1. SMS 발송 권한을 **사용자 단위 Boolean 플래그**로 모델링
2. 현재 시점에는 **정병오(N1001063)님만** 발송 가능
3. 권한 보유자는 **일일 한도 없이** 발송 가능 (signature_app이 서명요청/승인알림 등에서 충분히 쓰게)
4. 권한 없는 사용자는 **HTTP 403**으로 명확히 거절 + 한글 안내 메시지

다음 이터레이션에서 다룰 범위(**이 문서의 비범위**):

- admin-dashboard의 권한 관리 UI
- admin API (`PATCH /api/v1/users/{id}/send-sms`)
- 앱(deployed_apps) 단위의 세밀한 권한
- 일일/월간 할당량 세밀 제어, 메시지 템플릿 제한, 수신자 opt-out

## 2. 설계 결정 요약

| 결정 항목 | 선택 | 근거 |
|----------|------|------|
| 권한 모델 | User 모델에 Boolean 컬럼 `can_send_sms` 추가 | 기존 `can_deploy_apps`, `can_deploy_custom_auth` 패턴과 일관 |
| 기본값 | `default=False`, `server_default='false'` | 마이그레이션 적용 즉시 정병오님 외 모두 차단이 의도된 결과 |
| 초기 seed | Alembic 마이그레이션 내에서 N1001063만 `true`로 UPDATE | 즉시 목표 달성(한 번의 배포로 완결) |
| 한도 정책 | 권한 보유자는 일일 한도 bypass | "빠르게 허용"이 1차 릴리스 목적. YAGNI — 나중에 분리 필요해지면 별도 컬럼 추가 |
| 에러 응답 | `HTTP 403` + `"SMS 발송 권한이 없습니다. 관리자에게 문의하세요."` | 명확한 한글 안내, Pod의 `sms_worker`가 기존 실패 경로로 처리 |
| 인증 dependency | `get_current_user` → `get_current_user_or_pod` 교체 | Pod 호출(X-Pod-Name/X-Pod-Token) 수용. 기존 JWT 호출 경로도 그대로 작동 |
| Pod 측 변경 | `~/workspace/sms_worker.py` 헤더 교체 | `Authorization: Bearer <AUTH_TOKEN>` → `X-Pod-Name` + `X-Pod-Token: <SECURE_POD_TOKEN>`. Pod에 이미 주입된 env 사용 |
| admin UI | **이번 릴리스 제외** | 빠른 허용 우선. 추가 허용자는 DB `UPDATE` 수동 집행 |

## 3. 변경 지점

```
auth-gateway/
├── app/
│   ├── models/user.py              ← (A) can_send_sms 컬럼 추가
│   └── routers/sms.py              ← (B) send_sms() 상단 pre-check 삽입 + 한도 bypass
│                                     (E) dependency 교체: get_current_user → get_current_user_or_pod
├── alembic/versions/
│   └── XXXX_add_can_send_sms.py    ← (C) 컬럼 추가 + N1001063 seed
└── tests/routers/test_sms.py       ← (D) pre-check + 한도 bypass + Pod 인증 테스트 추가

정병오 Pod EFS:
└── /efs/users/n1001063/sms_worker.py  ← (F) 헤더 교체 (Authorization Bearer → X-Pod-Name/Token)
```

**변경하지 않는 것**:

- `admin-dashboard/*` (다음 이터레이션)
- `container-image/*` 이미지 레이어 (Pod 재빌드 불필요)
- `sms_logs` 테이블 스키마 (기존 감사 로그 유지)
- `get_current_user_or_pod` 자체 (이미 구현되어 있음 — 재사용만)

## 4. 상세 설계

### 4.1 데이터 모델 — `auth-gateway/app/models/user.py`

`can_deploy_custom_auth` 컬럼 바로 아래에 추가:

```python
can_send_sms = Column(
    Boolean,
    default=False,
    nullable=False,
    server_default='false',
)
```

### 4.2 Alembic 마이그레이션 — `auth-gateway/alembic/versions/XXXX_add_can_send_sms.py`

```python
"""add can_send_sms to users + seed N1001063

Revision ID: <generated>
Revises: <previous head>
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa

revision = "<generated>"
down_revision = "<previous head>"

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
    # 정병오(N1001063)님 초기 허용 — 1차 릴리스 정책
    op.execute("UPDATE users SET can_send_sms = true WHERE username = 'N1001063'")

def downgrade() -> None:
    op.drop_column("users", "can_send_sms")
```

### 4.3 API 변경 — `auth-gateway/app/routers/sms.py`

두 가지 변경:

1. **Import 및 dependency 교체**: `get_current_user` → `get_current_user_or_pod` (Pod 인증 수용). 이 dependency는 Bearer JWT 우선, 실패 시 `X-Pod-Name` + `X-Pod-Token` 헤더 쌍으로 fallback. 반환 dict는 두 경로 모두 `{"sub": username, ...}` 형태(Pod 경로에는 추가로 `"auth_type": "pod"`).
2. **권한 pre-check 추가 및 기존 `DAILY_LIMIT` 기반 일일 한도 검사 블록 제거**. `DAILY_LIMIT = 10` 모듈 상수 선언은 향후 권한-한도 분리 정책 도입 시 재사용을 위해 파일 상단에 유지하되, 미사용 상수임을 명시하는 주석을 추가.

```python
# import 변경
from app.core.security import get_current_user_or_pod  # 기존 get_current_user 대신

# 파일 상단 상수
DAILY_LIMIT = 10  # 현재는 사용되지 않음 — 향후 권한-한도 분리 정책 도입 시 재사용 예정

@router.post("/send", response_model=SmsSendResponse)
async def send_sms(
    request: SmsSendRequest,
    current_user: dict = Depends(get_current_user_or_pod),  # ← 교체
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    username = current_user["sub"]  # JWT/Pod 경로 모두 동일한 키

    # --- 신설: 권한 pre-check ---
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.can_send_sms:
        raise HTTPException(
            status_code=403,
            detail="SMS 발송 권한이 없습니다. 관리자에게 문의하세요.",
        )

    # (기존의 DAILY_LIMIT 기반 일일 카운트 검사 블록은 제거됨 —
    #  권한 보유자는 한도 없음이 1차 릴리스 정책)

    # --- 기존: 외부 SMS gateway 호출, sms_logs 기록 ---
    ...
```

**`GET /api/v1/sms/usage`** 엔드포인트도 Pod에서 호출될 수 있으므로 동일하게 `get_current_user_or_pod`로 교체 (일관성). 이 엔드포인트에는 pre-check를 적용하지 **않음** — 단순 조회는 차단 사유 없음.

부수효과 명시:

- `sms_logs` 테이블의 오늘자 발송 건수 조회 쿼리(기존 한도 검사에 쓰이던)는 함수 본문에서 제거됨
- `_get_today_count()` 헬퍼는 `/usage` 조회용으로 남겨둠 (삭제하지 않음)

에러 응답 계약:

| 상황 | HTTP | detail |
|------|------|--------|
| 권한 없음 | 403 | `"SMS 발송 권한이 없습니다. 관리자에게 문의하세요."` |
| 외부 gateway 오류 (기존) | 502 | (기존 유지) |
| 요청 형식 오류 (기존) | 422 | (기존 유지) |

### 4.4 테스트 — `auth-gateway/tests/routers/test_sms.py`

4개 케이스:

```python
def test_send_sms_403_when_no_permission(client, db_session, create_test_user, monkeypatch):
    # can_send_sms=False → 403
    create_test_user(username="TESTNOPERM", can_send_sms=False)
    monkeypatch_current_user(monkeypatch, username="TESTNOPERM")
    resp = client.post(
        "/api/v1/sms/send",
        json={"phone_number": "010-0000-0000", "message": "test"},
    )
    assert resp.status_code == 403
    assert "권한이 없습니다" in resp.json()["detail"]

def test_send_sms_ok_when_permission_granted(client, db_session, create_test_user, monkeypatch, mock_sms_gateway):
    # can_send_sms=True → 외부 게이트웨이 호출 성공 시 200
    create_test_user(username="TESTOK", can_send_sms=True)
    monkeypatch_current_user(monkeypatch, username="TESTOK")
    resp = client.post(
        "/api/v1/sms/send",
        json={"phone_number": "010-0000-0000", "message": "test"},
    )
    assert resp.status_code == 200

def test_send_sms_unlimited_for_permitted_user(client, db_session, create_test_user, monkeypatch, mock_sms_gateway):
    # 기존 DAILY_LIMIT=10 초과해도 11번째 요청 성공
    create_test_user(username="TESTOK", can_send_sms=True)
    monkeypatch_current_user(monkeypatch, username="TESTOK")
    for _ in range(11):
        resp = client.post(
            "/api/v1/sms/send",
            json={"phone_number": "010-0000-0000", "message": "test"},
        )
        assert resp.status_code == 200

def test_send_sms_ok_via_pod_auth(client, db_session, create_test_user, monkeypatch, mock_sms_gateway):
    # Pod 인증 경로(X-Pod-Name + X-Pod-Token) 으로도 권한 검사가 동작해야 함
    create_test_user(username="N1001063", can_send_sms=True)
    # conftest의 _mock_current_user가 Pod 경로도 동일 dict를 반환하도록 설정
    monkeypatch_current_user(monkeypatch, username="N1001063", auth_type="pod")
    resp = client.post(
        "/api/v1/sms/send",
        json={"phone_number": "010-0000-0000", "message": "test"},
    )
    assert resp.status_code == 200
```

기존 테스트 호환:

- `create_test_user` 팩토리 fixture(conftest.py:helpers)는 현재 `can_deploy_apps=True` 기본값 패턴 사용. **동일 패턴으로 `can_send_sms=True` 기본값 파라미터 추가**하여 기존 테스트 회귀 방지.
- `monkeypatch_current_user` 헬퍼가 conftest.py에 없으면 신설 (테스트용 `_mock_current_user` override 변경).
- `mock_sms_gateway` 픽스처 신설: `httpx.AsyncClient.post` 를 monkeypatch 하여 외부 SMS 게이트웨이를 호출하지 않고 `{"Result": "00", "ResultMsg": "OK"}` 응답 반환.

### 4.5 Pod 측 — `~/workspace/sms_worker.py` (정병오 Pod 내)

`AUTH_TOKEN` 환경변수가 비어 있으므로 기존 `Authorization: Bearer ${AUTH_TOKEN}` 헤더로는 인증 실패. 대신 Pod 기동 시 K8s Secret → env로 자동 주입되는 `SECURE_POD_TOKEN` 과 `HOSTNAME`(= pod 이름 `claude-terminal-n1001063`) 을 사용하여 두 헤더로 인증한다.

위치: EFS 영구 저장 (`/efs/users/n1001063/sms_worker.py` ≡ Pod 내부 `/home/node/workspace/sms_worker.py`). **Pod 재시작/이미지 재빌드 불필요** — 파일만 교체 후 프로세스 재시작으로 반영.

변경 diff 요지:

```python
# 기존
token = os.environ.get("AUTH_TOKEN", "")
req = urllib.request.Request(
    SMS_API_URL,
    data=body,
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
)

# 변경
pod_token = os.environ.get("SECURE_POD_TOKEN", "")
pod_name = os.environ.get("HOSTNAME", "")  # claude-terminal-n1001063
req = urllib.request.Request(
    SMS_API_URL,
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-Pod-Name": pod_name,
        "X-Pod-Token": pod_token,
    },
)
```

적용 절차:

1. 정병오 Pod 내 터미널에서 `~/workspace/sms_worker.py` 수정 (또는 관리자 `kubectl exec`)
2. 실행 중인 워커 프로세스 종료 후 재시작 (signature_app 의 lifespan 핸들러가 처리하거나, 직접 `pkill -f sms_worker.py` → `python3 ~/workspace/sms_worker.py &`)
3. `~/workspace/sms_queue/` 에 테스트 task 하나 투입하여 `.result.json` 이 `{"ok": true, ...}` 로 나오는지 확인

## 5. 배포 및 검증

### 5.1 배포 순서

1. PR 생성 및 리뷰 → main 머지
2. auth-gateway 배포 파이프라인 실행
3. Alembic `upgrade head` 자동 실행: 컬럼 추가 + N1001063 seed
4. auth-gateway Pod rolling restart
5. 정병오 Pod 내 `~/workspace/sms_worker.py` 파일 교체 + 워커 재시작 (Pod 재생성 불필요)

### 5.2 검증 체크리스트

- [ ] Pod 인증 경로: 정병오 Pod 내에서 `curl -H "X-Pod-Name: $HOSTNAME" -H "X-Pod-Token: $SECURE_POD_TOKEN" -X POST http://auth-gateway.platform:8000/api/v1/sms/send -d '{"phone_number":"010-xxxx-xxxx","message":"test"}'` → HTTP 200
- [ ] JWT 경로: N1001063 브라우저 JWT로 `POST /api/v1/sms/send` → HTTP 200
- [ ] 테스트용 다른 사번으로 호출 → HTTP 403 + 한글 메시지 확인
- [ ] N1001063으로 11회 연속 발송 → 전부 200 (한도 bypass 확인)
- [ ] `sms_logs` 테이블에 sender=`N1001063` 발송 내역 정상 적재
- [ ] 정병오 `signature_app` 의 "📱 SMS 전송" 버튼 → 큐 파일 → 워커 → auth-gateway → SMS 수신까지 E2E 1회 성공

### 5.3 롤백

- auth-gateway를 이전 이미지 태그로 되돌림 (컬럼은 drop하지 않아도 이전 코드 동작에 영향 없음. 필요 시 `alembic downgrade -1`)
- `sms_worker.py` 는 git 기록 없으므로 수정 전 원본을 `sms_worker.py.bak` 으로 백업 후 변경 (롤백 시 mv 복원)

## 6. 후속 이터레이션(본 문서 비범위) 예고

- admin-dashboard의 사용자 관리 페이지에 `can_send_sms` 토글 UI 추가
- `PATCH /api/v1/users/{user_id}/send-sms` 엔드포인트 (`_require_admin` 기반)
- 권한-한도 분리가 필요해지면 `sms_daily_limit_override` 컬럼 추가
- 앱(`deployed_apps`) 단위 세밀 권한 (`sms_allowed`) — 현재는 사용자 단위로 충분

## 7. 미해결(Open) 항목

없음. 1차 릴리스는 본 문서로 완결.
