# Phase 1a: Silent SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hub 로그인된 사용자가 `ai-chat.skons.net` 클릭 시 **재로그인 없이** 통과. `webui-verify`가 access 쿠키 만료 시 refresh 쿠키로 조용히 재발급.

**Architecture:** `auth-gateway`의 `webui_verify` 엔드포인트에 silent refresh 로직을 추가하고, ingress-nginx `auth-response-headers`에 `Set-Cookie`를 포함시켜 재발급 쿠키가 클라이언트에 전달되도록 한다. 기존 `bedrock_jwt`(15분) + `bedrock_refresh`(12시간) 2-tier cookie 스킴 유지.

**Tech Stack:** FastAPI · Python 3.12 · ingress-nginx · 기존 RS256 JWT · 기존 Redis revocation check · pytest

**관련 스펙:** `docs/superpowers/specs/2026-04-15-unified-workspace-arch-b-design.md` §6

---

## File Structure

**Modify:**
- `auth-gateway/app/routers/auth.py` — `webui_verify` 함수에 silent refresh 로직 추가
- `infra/k8s/openwebui/ingress.yaml` — `auth-response-headers`에 `Set-Cookie` 추가

**Create:**
- `auth-gateway/tests/test_webui_verify_silent_refresh.py` — 신규 테스트 4종

**Verify (no code):**
- 운영 ingress-nginx 버전에서 auth_request Set-Cookie 전파 동작 확인

---

## Task 1: Silent refresh 실패 테스트 — 둘 다 없음

**Files:**
- Create: `auth-gateway/tests/test_webui_verify_silent_refresh.py`

- [ ] **Step 1: 테스트 파일 생성 + 첫 테스트 작성**

```python
"""Phase 1a: webui-verify silent refresh — ai-chat.skons.net 재로그인 박멸."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_webui_verify_no_cookies_returns_401():
    """쿠키 전혀 없음 → 401 Bearer."""
    resp = client.get("/api/v1/auth/webui-verify")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")
```

- [ ] **Step 2: 테스트 실행 — PASS 확인 (기존 동작 유지)**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/auth-gateway
PYTHONPATH=. pytest tests/test_webui_verify_silent_refresh.py::test_webui_verify_no_cookies_returns_401 -v
```

Expected: PASS (기존 webui_verify가 이미 이 동작)

- [ ] **Step 3: 커밋**

```bash
git add auth-gateway/tests/test_webui_verify_silent_refresh.py
git commit -m "test(auth): webui-verify silent refresh — 쿠키 없음 401 회귀 가드"
```

---

## Task 2: Silent refresh 실패 테스트 — refresh 만료

**Files:**
- Modify: `auth-gateway/tests/test_webui_verify_silent_refresh.py`

- [ ] **Step 1: 만료된 refresh 토큰 테스트 추가**

기존 파일 끝에 추가:
```python
from datetime import timedelta

from app.core.jwt_rs256 import create_refresh_token
from app.core.config import get_settings


def test_webui_verify_expired_refresh_returns_401(create_test_user):
    """refresh 만료 → 401 (auth-signin으로 리다이렉트)."""
    user = create_test_user(username="N1102359")
    settings = get_settings()
    # 이미 만료된 refresh token 생성 (expires_delta=-1초)
    expired_refresh = create_refresh_token(
        user.username, "", "", "user", settings,
        expires_delta=timedelta(seconds=-1),
    )
    resp = client.get(
        "/api/v1/auth/webui-verify",
        cookies={"bedrock_refresh": expired_refresh},
    )
    assert resp.status_code == 401
    # 재발급되지 않아야 함
    assert "Set-Cookie" not in resp.headers or "bedrock_jwt=" not in resp.headers.get("Set-Cookie", "")
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인 (silent refresh 미구현)**

```bash
PYTHONPATH=. pytest tests/test_webui_verify_silent_refresh.py::test_webui_verify_expired_refresh_returns_401 -v
```

Expected: PASS 또는 FAIL. 현재는 webui_verify가 `bedrock_refresh`를 보지 않으므로 refresh 존재 여부와 무관하게 401. 이 테스트는 PASS (기존 동작과 호환). 중요한 건 "만료 refresh로 재발급 안 됨"을 문서화.

- [ ] **Step 3: 커밋**

```bash
git add auth-gateway/tests/test_webui_verify_silent_refresh.py
git commit -m "test(auth): webui-verify silent refresh — 만료 refresh 401 가드"
```

---

## Task 3: Silent refresh 성공 테스트 — 재발급 경로

**Files:**
- Modify: `auth-gateway/tests/test_webui_verify_silent_refresh.py`

- [ ] **Step 1: 유효 refresh로 재발급 성공 테스트 추가**

```python
def test_webui_verify_expired_access_valid_refresh_silently_reissues(create_test_user):
    """access 만료 + refresh 유효 → 200 + Set-Cookie(bedrock_jwt)."""
    user = create_test_user(username="N1102359")
    settings = get_settings()
    # 만료된 access
    expired_access = create_access_token(
        user.username, "", "", "user", settings,
        expires_delta=timedelta(seconds=-1),
    )
    # 유효한 refresh (12h)
    valid_refresh = create_refresh_token(
        user.username, "", "", "user", settings,
    )
    resp = client.get(
        "/api/v1/auth/webui-verify",
        cookies={
            "bedrock_jwt": expired_access,
            "bedrock_refresh": valid_refresh,
        },
    )
    assert resp.status_code == 200
    # 새 access cookie가 Set-Cookie로 내려와야 함
    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "bedrock_jwt=" in set_cookie, f"Expected new bedrock_jwt cookie, got: {set_cookie!r}"
    # X-SKO-Email 헤더 정상
    assert resp.headers.get("X-SKO-Email") == f"{user.username}@skons.net"
```

Import 추가 (파일 상단):
```python
from app.core.jwt_rs256 import create_access_token, create_refresh_token
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
PYTHONPATH=. pytest tests/test_webui_verify_silent_refresh.py::test_webui_verify_expired_access_valid_refresh_silently_reissues -v
```

Expected: FAIL — 현재 webui_verify는 만료 access → 즉시 401. silent refresh 미구현.

- [ ] **Step 3: 커밋 (실패 테스트)**

```bash
git add auth-gateway/tests/test_webui_verify_silent_refresh.py
git commit -m "test(auth): webui-verify silent refresh — 재발급 성공 실패 테스트"
```

---

## Task 4: Silent refresh 구현

**Files:**
- Modify: `auth-gateway/app/routers/auth.py` — `webui_verify` 함수 (현재 line 552-630)

- [ ] **Step 1: import 추가 (함수 밖, 파일 상단부)**

`auth-gateway/app/routers/auth.py` 기존 import 블록에 추가:

```python
from app.core.jwt_rs256 import (
    _verify_jwt_signature_only,
    create_access_token as jwt_create_access_token,
    is_jti_blacklisted,
)
from app.routers.jwt_auth import write_access_cookies, REFRESH_COOKIE_NAME
```

(이미 있는 import는 중복 X — 기존 라인 확인 후 필요한 것만 추가)

- [ ] **Step 2: webui_verify 함수에 silent refresh 로직 추가**

`webui_verify` 함수 (line 552 근처) 수정. 기존 401 발생 직전에 silent refresh 시도 로직 삽입:

```python
@router.get("/webui-verify", status_code=200)
async def webui_verify(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
):
    """nginx ingress auth_request 콜백 — Open WebUI SSO 연동.

    검증 흐름:
      1. bedrock_jwt access 쿠키 유효 → 200 + X-SKO-Email (기존)
      2. access 만료/없음 + bedrock_refresh 유효 → silent refresh:
         새 access 발급 → Set-Cookie → 200 + X-SKO-Email
      3. 둘 다 무효 → 401 (auth-signin 경로 실행)

    Silent refresh는 refresh jti를 blacklist하지 않는다.
    Hub의 /auth/refresh 엔드포인트만 blacklist+rotation을 담당한다.
    (두 경로 동시 호출 시 replay 오탐 방지)
    """
    from app.core.jwt_rs256 import is_user_revoked
    from app.core.security import decode_token

    token = request.cookies.get("bedrock_jwt", "") or request.cookies.get("claude_token", "")
    payload = decode_token(token, settings) if token else None

    if payload:
        # 경로 1: access 유효
        username = payload.get("sub", "")
        if not username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing subject",
                headers={"WWW-Authenticate": 'Bearer realm="skons.net"'},
            )
        try:
            if is_user_revoked(username):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session has been revoked",
                    headers={"WWW-Authenticate": 'Bearer realm="skons.net"'},
                )
        except HTTPException:
            raise
        except Exception:
            pass  # Redis 장애 시 JWT 서명만으로 통과

        response.headers["X-SKO-Email"] = f"{username}@skons.net"
        response.headers["X-SKO-User-Id"] = username
        return {"ok": True}

    # 경로 2: silent refresh 시도
    raw_refresh = request.cookies.get(REFRESH_COOKIE_NAME, "")
    if raw_refresh:
        from jose import JWTError  # lazy import

        try:
            refresh_payload = _verify_jwt_signature_only(raw_refresh, expected_type="refresh")
        except JWTError:
            refresh_payload = None

        if refresh_payload:
            sub = refresh_payload.get("sub", "")
            emp_no = refresh_payload.get("emp_no", "")
            email = refresh_payload.get("email", "")
            role = refresh_payload.get("role", "user")
            jti = refresh_payload.get("jti", "")

            # 사용자 revoke / jti blacklist 체크 (두 모두 통과해야 함)
            revoked = False
            try:
                revoked = is_user_revoked(sub) or (bool(jti) and is_jti_blacklisted(jti))
            except Exception:
                revoked = False  # Redis 장애 시 서명만으로 통과

            if sub and not revoked:
                # 새 access token 발급 — refresh는 그대로 유지 (Hub /auth/refresh가 rotation 담당)
                new_access = jwt_create_access_token(sub, emp_no, email, role, settings)
                write_access_cookies(response, new_access)

                response.headers["X-SKO-Email"] = f"{sub}@skons.net"
                response.headers["X-SKO-User-Id"] = sub
                return {"ok": True}

    # 경로 3: 둘 다 무효 → 401
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": 'Bearer realm="skons.net"'},
    )
```

- [ ] **Step 3: 테스트 실행 — PASS 확인**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/auth-gateway
PYTHONPATH=. pytest tests/test_webui_verify_silent_refresh.py -v
```

Expected: 3/3 PASS

- [ ] **Step 4: 회귀 검사 — 기존 webui-verify 테스트 통과**

```bash
PYTHONPATH=. pytest tests/test_www_authenticate_bearer.py -v
```

Expected: 모든 기존 테스트 PASS

- [ ] **Step 5: 커밋**

```bash
git add auth-gateway/app/routers/auth.py
git commit -m "feat(auth): webui-verify silent refresh — ai-chat 재로그인 제거"
```

---

## Task 5: 추가 테스트 — revoke 차단

**Files:**
- Modify: `auth-gateway/tests/test_webui_verify_silent_refresh.py`

- [ ] **Step 1: revoked refresh 테스트 추가**

```python
def test_webui_verify_revoked_refresh_returns_401(create_test_user, monkeypatch):
    """refresh jti가 blacklist에 있으면 401 (silent refresh 거부)."""
    user = create_test_user(username="N1102359")
    settings = get_settings()
    expired_access = create_access_token(
        user.username, "", "", "user", settings,
        expires_delta=timedelta(seconds=-1),
    )
    valid_refresh = create_refresh_token(user.username, "", "", "user", settings)

    # jti blacklist 시뮬레이션 — is_jti_blacklisted를 True로 patch
    monkeypatch.setattr(
        "app.routers.auth.is_jti_blacklisted",
        lambda jti: True,
    )

    resp = client.get(
        "/api/v1/auth/webui-verify",
        cookies={
            "bedrock_jwt": expired_access,
            "bedrock_refresh": valid_refresh,
        },
    )
    assert resp.status_code == 401
    # 재발급 쿠키 없어야 함
    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "bedrock_jwt=" not in set_cookie
```

- [ ] **Step 2: 테스트 실행**

```bash
PYTHONPATH=. pytest tests/test_webui_verify_silent_refresh.py::test_webui_verify_revoked_refresh_returns_401 -v
```

Expected: PASS

- [ ] **Step 3: 커밋**

```bash
git add auth-gateway/tests/test_webui_verify_silent_refresh.py
git commit -m "test(auth): webui-verify silent refresh — revoked jti 401 가드"
```

---

## Task 6: Ingress auth-response-headers 확장

**Files:**
- Modify: `infra/k8s/openwebui/ingress.yaml`

- [ ] **Step 1: auth-response-headers에 Set-Cookie 추가**

`infra/k8s/openwebui/ingress.yaml`의 `nginx.ingress.kubernetes.io/auth-response-headers` 라인을 수정:

**기존:**
```yaml
    nginx.ingress.kubernetes.io/auth-response-headers: "X-SKO-Email,X-SKO-User-Id"
```

**신규:**
```yaml
    # Silent SSO: webui-verify가 silent refresh 시 발급하는 새 bedrock_jwt 쿠키를
    # 최종 클라이언트 응답에 전달하기 위해 Set-Cookie 포함 (spec §6.2).
    nginx.ingress.kubernetes.io/auth-response-headers: "X-SKO-Email,X-SKO-User-Id,Set-Cookie"
```

- [ ] **Step 2: 변경 커밋**

```bash
git add infra/k8s/openwebui/ingress.yaml
git commit -m "feat(ingress): auth-response-headers에 Set-Cookie 포함 — silent SSO 전파"
```

---

## Task 7: 컨테이너 이미지 빌드·푸시

**Files:** (코드 변경 없음)

- [ ] **Step 1: ECR 로그인**

```bash
aws ecr get-login-password --region ap-northeast-2 | \
  docker login --username AWS --password-stdin 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com
```

Expected: `Login Succeeded`

- [ ] **Step 2: amd64 이미지 빌드**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/auth-gateway
docker build --platform linux/amd64 \
  -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest .
```

Expected: 성공 (마지막 줄 `naming to ...:latest done`)

- [ ] **Step 3: 푸시**

```bash
docker push 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest
```

Expected: `latest: digest: sha256:... size: ...`

---

## Task 8: K8s 배포

- [ ] **Step 1: Ingress 적용**

```bash
kubectl apply -f /Users/cation98/Project/bedrock-ai-agent/infra/k8s/openwebui/ingress.yaml
```

Expected: `ingress.networking.k8s.io/open-webui-ingress configured`

- [ ] **Step 2: auth-gateway rollout restart**

```bash
kubectl rollout restart deploy/auth-gateway -n platform
kubectl rollout status deploy/auth-gateway -n platform --timeout=180s
```

Expected: `deployment "auth-gateway" successfully rolled out`

- [ ] **Step 3: 신규 Pod 준비 상태 확인**

```bash
kubectl get pods -n platform -l app=auth-gateway
```

Expected: 2/2 READY, STATUS Running

---

## Task 9: Set-Cookie 전파 E2E 검증

**사전 조건:** 만료된 bedrock_jwt + 유효한 bedrock_refresh를 생성할 수 있어야 함.

- [ ] **Step 1: 테스트 토큰 스크립트로 만료 access + 유효 refresh 생성**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/auth-gateway
PYTHONPATH=. python -c "
from datetime import timedelta
from app.core.jwt_rs256 import create_access_token, create_refresh_token
from app.core.config import get_settings
s = get_settings()
expired = create_access_token('N1102359', '', '', 'user', s, expires_delta=timedelta(seconds=-1))
valid_ref = create_refresh_token('N1102359', '', '', 'user', s)
print('EXPIRED_ACCESS=' + expired)
print('VALID_REFRESH=' + valid_ref)
" | tee /tmp/webui-verify-tokens.txt
```

Expected: 두 토큰이 stdout에 출력됨

- [ ] **Step 2: auth-gateway 직접 호출 — silent refresh 동작 검증**

```bash
source /tmp/webui-verify-tokens.txt
curl -sv "https://claude.skons.net/api/v1/auth/webui-verify" \
  -H "Cookie: bedrock_jwt=${EXPIRED_ACCESS}; bedrock_refresh=${VALID_REFRESH}" \
  2>&1 | grep -E "HTTP/|< set-cookie|< x-sko"
```

Expected:
- `< HTTP/1.1 200` 또는 `< HTTP/2 200`
- `< set-cookie: bedrock_jwt=...; ...`
- `< x-sko-email: N1102359@skons.net`

- [ ] **Step 3: ai-chat 경로 검증 — Set-Cookie가 최종 클라이언트에 전달되는지**

```bash
source /tmp/webui-verify-tokens.txt
curl -sv "https://ai-chat.skons.net/" \
  -H "Cookie: bedrock_jwt=${EXPIRED_ACCESS}; bedrock_refresh=${VALID_REFRESH}" \
  2>&1 | grep -iE "HTTP/|< set-cookie"
```

Expected (성공):
- HTTP 200 또는 리다이렉트
- `< Set-Cookie: bedrock_jwt=...` (재발급 쿠키가 클라이언트에 도달)

**실패 시**: Set-Cookie가 없으면 ingress-nginx가 auth_request Set-Cookie를 전파하지 않는다는 뜻 → Task 10 Plan B로 전환.

---

## Task 10: 결과에 따른 분기

### 10A: Task 9가 성공한 경우

- [ ] **Step 1: 운영 사용자 smoke test (본인 계정)**

브라우저에서:
1. DevTools Network 탭 열고 쿠키 창 확인 — `bedrock_jwt` exp 시간 메모
2. `claude.skons.net` 이미 로그인 상태 유지 (또는 `/login` 재로그인)
3. 15분 이상 대기 (access 만료 대기) — 또는 개발 도구로 `bedrock_jwt` 쿠키 삭제
4. `ai-chat.skons.net` 직접 접속 (새 탭)
5. **기대**: 로그인 페이지 없이 Open WebUI 채팅 화면 도달
6. DevTools Network에서 `webui-verify` 요청의 Response Headers에 `set-cookie: bedrock_jwt=...` 확인
7. 이후 `document.cookie`에 새 `bedrock_jwt_vis` 반영

- [ ] **Step 2: 완료 커밋 (변경사항 없으면 skip)**

```bash
git log --oneline -n 5  # 지금까지의 커밋 요약
```

Phase 1a 종료. Phase 1b plan 작성으로 진행.

### 10B: Task 9가 실패한 경우 (Set-Cookie 미전파)

- [ ] **Step 1: ingress-nginx 버전 확인**

```bash
kubectl -n ingress-nginx exec deploy/ingress-nginx-controller -- /nginx-ingress-controller --version 2>&1 | head
```

- [ ] **Step 2: Plan B (SSO-3) 전환 결정 기록**

`docs/decisions/2026-04-15-silent-sso-fallback.md`를 작성:
- 원인 (ingress-nginx vX.Y.Z는 auth_request Set-Cookie 미전파)
- 전환 방향 (SSO-3: `bedrock_webui_session` 장기 쿠키 신설)
- Phase 1a 재계획 요약 (Phase 1a-v2 plan 별도 작성 필요)

- [ ] **Step 3: 커밋 + Phase 1a-v2 plan 요청**

```bash
git add docs/decisions/2026-04-15-silent-sso-fallback.md
git commit -m "docs(decision): silent SSO SSO-1 미지원 → SSO-3 전환"
```

---

## Acceptance Criteria

- [ ] `pytest tests/test_webui_verify_silent_refresh.py` 4/4 PASS
- [ ] `pytest tests/test_www_authenticate_bearer.py` 회귀 없음
- [ ] 운영 auth-gateway Pod에 신규 이미지 rollout 완료
- [ ] Ingress auth-response-headers에 `Set-Cookie` 포함됨
- [ ] curl E2E: 만료 access + 유효 refresh → 200 + Set-Cookie + X-SKO-Email
- [ ] 브라우저 smoke test: Hub 로그인 후 15분+ 경과 → ai-chat 클릭 시 재로그인 없음
- [ ] 스펙 §6 완료 기준 충족

## Rollback

- Ingress만 되돌리기: `git revert <ingress 커밋>` → `kubectl apply`
- auth-gateway 이전 이미지로 복귀: `kubectl rollout undo deploy/auth-gateway -n platform`
- 최악의 경우: access TTL 8시간으로 임시 연장 (docs/decisions/phase1a-samesite-strict-vs-lax.md 재검토 필요)
