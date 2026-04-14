# Phase 1b-A: WebUI → Pod Bridge (Arch-B MVP)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Open WebUI(ai-chat.skons.net) 메시지를 **사용자 본인의 Claude terminal Pod**로 라우팅하여, Claude Code가 사용자의 `~/.claude/` 스킬·설정·파일을 그대로 활용해 응답하도록 한다. 결과: 양방향 자원 공유(L2) + 스킬 양방향 호환성(X1).

**Architecture:** 사용자 Pod에 `webui-bridge`(Python FastAPI + Claude Agent SDK) 프로세스 추가, 포트 7682 cluster-internal. Open WebUI에 `user_pod_pipe` 신규 등록. pipe가 사번으로 Pod URL 계산 → bridge HTTP POST → bridge가 Agent SDK 호출(HOME=/home/node) → SSE 응답 중계.

**Tech Stack:** Python 3.12 · FastAPI · `claude-agent-sdk` · Open WebUI Pipelines · K8s Secret · NetworkPolicy

**관련 스펙:** `docs/superpowers/specs/2026-04-15-unified-workspace-arch-b-design.md` §7 (7.1.1, 7.1.3, 7.2, 7.4)

**비포함 (후속 plan):**
- Tier 1 로깅 pipeline (S3/Firehose/Fluent Bit) → `2026-04-15-phase1b-b-logging.md`
- 사용자 통지 + ToS 동의 → `2026-04-15-phase1b-c-consent.md`

---

## File Structure

**Create:**
- `container-image/webui-bridge/__init__.py`
- `container-image/webui-bridge/app.py` — FastAPI application
- `container-image/webui-bridge/requirements.txt`
- `infra/k8s/openwebui/user-pod-pipe.py` — OWUI pipe 소스
- `infra/k8s/platform/webui-bridge-secret-template.yaml.example`
- `infra/k8s/platform/webui-bridge-netpol.yaml` — Bridge ingress 제한

**Modify:**
- `container-image/Dockerfile` — bridge 코드 + 의존성 + 포트 노출
- `container-image/entrypoint.sh` — bridge 프로세스 기동
- `infra/k8s/pod-template.yaml` (또는 `auth-gateway/app/services/k8s_service.py` 내 Pod spec 생성 로직) — 7682 포트 노출 + env 주입
- `infra/k8s/openwebui/openwebui-pipelines.yaml` — pipe 등록

---

## Task 1: Bridge FastAPI 스켈레톤

**Files:**
- Create: `container-image/webui-bridge/__init__.py` (빈 파일)
- Create: `container-image/webui-bridge/app.py`
- Create: `container-image/webui-bridge/requirements.txt`

- [ ] **Step 1: requirements.txt 작성**

```
fastapi==0.115.4
uvicorn[standard]==0.32.0
claude-agent-sdk==0.2.0
pydantic==2.9.2
```

- [ ] **Step 2: `container-image/webui-bridge/app.py` 작성**

```python
"""WebUI ↔ User Pod Bridge — Arch-B MVP.

WebUI가 사용자의 Claude Pod 본인 내부 프로세스로 HTTP 요청.
Agent SDK가 HOME=/home/node로 실행되어 ~/.claude/ 스킬·설정 자동 발견.
"""
import asyncio
import json
import os
from typing import AsyncGenerator

from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Agent SDK (lazy import — 테스트 용이성)
try:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
except ImportError:  # 빌드 없이 pytest 실행 시 skip
    ClaudeSDKClient = None
    ClaudeAgentOptions = None


OWNER_USERNAME = os.environ["OWNER_USERNAME"]
BRIDGE_SECRET = os.environ["WEBUI_BRIDGE_SECRET"]
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/home/node/workspace")

app = FastAPI(title="WebUI Bridge")


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    session_id: str
    messages: list[ChatMessage]
    model: str = "us.anthropic.claude-sonnet-4-6"
    stream: bool = True


@app.get("/health")
def health():
    return {"status": "ok", "owner": OWNER_USERNAME}


def _verify_auth(authorization: str | None, x_sko_user_id: str | None) -> None:
    """Bearer secret + owner 일치 검증."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required")
    if authorization.removeprefix("Bearer ") != BRIDGE_SECRET:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bridge secret")
    if x_sko_user_id != OWNER_USERNAME:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"User mismatch: expected {OWNER_USERNAME!r}",
        )


async def _agent_stream(req: ChatRequest) -> AsyncGenerator[str, None]:
    """Claude Agent SDK를 호출하여 SSE 이벤트 스트림 생성."""
    if ClaudeSDKClient is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "SDK unavailable")

    # 사용자 메시지만 이어붙여 Claude에 전달
    # (system/history 처리는 Phase 1b-B에서 히스토리 통합 시 확장)
    user_text = "\n\n".join(
        m.content for m in req.messages if m.role == "user"
    )

    options = ClaudeAgentOptions(
        cwd=WORKSPACE_DIR,
        model=req.model,
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_text)
        async for event in client.receive_response():
            payload = json.dumps(event, default=str, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"


@app.post("/webui/chat")
async def webui_chat(
    req: ChatRequest,
    authorization: str | None = Header(default=None),
    x_sko_user_id: str | None = Header(default=None, alias="X-SKO-User-Id"),
):
    _verify_auth(authorization, x_sko_user_id)
    return StreamingResponse(_agent_stream(req), media_type="text/event-stream")
```

- [ ] **Step 3: 커밋**

```bash
git add container-image/webui-bridge/
git commit -m "feat(bridge): WebUI→Pod bridge FastAPI 스켈레톤 (7682/webui/chat SSE)"
```

---

## Task 2: Bridge 로컬 단위 테스트

**Files:**
- Create: `tests/unit/test_webui_bridge.py`

- [ ] **Step 1: 테스트 작성 — 인증 검증**

```python
"""webui-bridge 인증 검증 테스트."""
import os
os.environ["OWNER_USERNAME"] = "TESTUSER01"
os.environ["WEBUI_BRIDGE_SECRET"] = "test-secret-xyz"
os.environ["WORKSPACE_DIR"] = "/tmp"

from fastapi.testclient import TestClient

import sys
sys.path.insert(0, "container-image")
from webui_bridge.app import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "owner": "TESTUSER01"}


def test_chat_missing_auth_returns_401():
    r = client.post(
        "/webui/chat",
        json={"session_id": "s1", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401


def test_chat_wrong_secret_returns_401():
    r = client.post(
        "/webui/chat",
        headers={"Authorization": "Bearer WRONG", "X-SKO-User-Id": "TESTUSER01"},
        json={"session_id": "s1", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401


def test_chat_user_mismatch_returns_403():
    r = client.post(
        "/webui/chat",
        headers={"Authorization": "Bearer test-secret-xyz", "X-SKO-User-Id": "OTHER"},
        json={"session_id": "s1", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 403
```

- [ ] **Step 2: 실행**

프로젝트 루트에서:
```bash
cd /Users/cation98/Project/bedrock-ai-agent
pip install fastapi pydantic "uvicorn[standard]" httpx pytest --quiet
PYTHONPATH=container-image:. pytest tests/unit/test_webui_bridge.py -v
```

Expected: 4/4 PASS (Agent SDK 호출은 스킵됨 — lazy import)

- [ ] **Step 3: 커밋**

```bash
git add tests/unit/test_webui_bridge.py
git commit -m "test(bridge): webui-bridge 인증 4종 가드"
```

---

## Task 3: Dockerfile 통합 — bridge + SDK 설치

**Files:**
- Modify: `container-image/Dockerfile`

- [ ] **Step 1: Dockerfile에 bridge 복사 + pip 설치 + 포트 노출**

`container-image/Dockerfile`의 Python 의존성 설치 블록을 찾아 아래를 추가 (기존 `COPY scripts/share-sync.sh ...` 근처가 적당):

```dockerfile
# WebUI Bridge (Arch-B) — Claude Agent SDK 기반 HTTP 서버
COPY webui-bridge /opt/webui-bridge
RUN pip install --no-cache-dir -r /opt/webui-bridge/requirements.txt
```

그리고 기존 `EXPOSE 7681 8080 3000` 라인을 수정:
```dockerfile
EXPOSE 7681 7682 8080 3000
```

- [ ] **Step 2: 커밋**

```bash
git add container-image/Dockerfile
git commit -m "build(bridge): Dockerfile에 webui-bridge 설치 + 7682 포트 노출"
```

---

## Task 4: entrypoint.sh에서 bridge 기동

**Files:**
- Modify: `container-image/entrypoint.sh`

- [ ] **Step 1: fileserver 기동 바로 앞에 bridge 기동 추가**

`python3 /usr/local/bin/fileserver.py --port "${FILE_SERVER_PORT}" --dir /home/node/workspace &` 라인 **직전**에 다음 블록 삽입:

```bash
# WebUI Bridge (Arch-B) — 사용자 Pod 내부 Claude Code API
# 포트 7682, cluster-internal only (NetworkPolicy로 제한)
if [ -n "${WEBUI_BRIDGE_SECRET:-}" ] && [ -n "${OWNER_USERNAME:-}" ]; then
    export WORKSPACE_DIR="/home/node/workspace"
    (uvicorn webui_bridge.app:app \
        --host 0.0.0.0 --port 7682 \
        --app-dir /opt \
        > /var/log/webui-bridge.log 2>&1) &
    echo "webui-bridge started on port 7682 (owner=${OWNER_USERNAME})"
else
    echo "webui-bridge disabled (WEBUI_BRIDGE_SECRET or OWNER_USERNAME missing)"
fi
```

- [ ] **Step 2: 커밋**

```bash
git add container-image/entrypoint.sh
git commit -m "feat(bridge): entrypoint.sh에서 uvicorn bridge 기동"
```

---

## Task 5: K8s Secret + Pod spec 업데이트 — BRIDGE_SECRET 주입

**Files:**
- Create: `infra/k8s/platform/webui-bridge-secret-template.yaml.example`
- Modify: Pod 생성 로직 (`auth-gateway/app/services/k8s_service.py` 또는 `infra/k8s/pod-template.yaml`)

- [ ] **Step 1: Secret 템플릿 생성 (example)**

`infra/k8s/platform/webui-bridge-secret-template.yaml.example`:
```yaml
# 공유 secret — 모든 사용자 Pod에 동일 값 주입 (Open WebUI pipe와 일치)
# 실제 배포는 Terraform random_password 또는 kubectl create secret
apiVersion: v1
kind: Secret
metadata:
  name: webui-bridge-secret
  namespace: claude-sessions
type: Opaque
stringData:
  secret: REPLACE_WITH_RANDOM_32B_HEX
```

- [ ] **Step 2: 운영 Secret 생성 (수동 1회)**

```bash
SECRET=$(openssl rand -hex 32)
kubectl create secret generic webui-bridge-secret \
  --from-literal=secret="${SECRET}" \
  -n claude-sessions
# Open WebUI namespace에도 동일 값 배포 (pipe가 사용)
kubectl create secret generic webui-bridge-secret \
  --from-literal=secret="${SECRET}" \
  -n openwebui
echo "Secret deployed. Keep in 1Password."
```

- [ ] **Step 3: Pod spec에 secret + OWNER_USERNAME env 주입**

`auth-gateway/app/services/k8s_service.py`의 `create_pod` 함수에서 container env 추가:

```python
V1EnvVar(name="OWNER_USERNAME", value=username),
V1EnvVar(
    name="WEBUI_BRIDGE_SECRET",
    value_from=V1EnvVarSource(
        secret_key_ref=V1SecretKeySelector(
            name="webui-bridge-secret",
            key="secret",
        ),
    ),
),
```

(정확한 import + 기존 env 리스트 확인 후 통합)

- [ ] **Step 4: 커밋**

```bash
git add infra/k8s/platform/webui-bridge-secret-template.yaml.example auth-gateway/app/services/k8s_service.py
git commit -m "feat(bridge): Pod에 OWNER_USERNAME + WEBUI_BRIDGE_SECRET env 주입"
```

---

## Task 6: NetworkPolicy — Bridge ingress 제한

**Files:**
- Create: `infra/k8s/platform/webui-bridge-netpol.yaml`

- [ ] **Step 1: NetworkPolicy 작성**

```yaml
# webui-bridge 포트 7682는 Open WebUI pipe pod만 접속 허용
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: webui-bridge-ingress
  namespace: claude-sessions
spec:
  podSelector:
    matchLabels:
      app: claude-terminal
  policyTypes:
  - Ingress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: openwebui
      podSelector:
        matchLabels:
          app: open-webui-pipelines
    ports:
    - protocol: TCP
      port: 7682
```

- [ ] **Step 2: 적용**

```bash
kubectl apply -f infra/k8s/platform/webui-bridge-netpol.yaml
```

Expected: `networkpolicy.networking.k8s.io/webui-bridge-ingress created`

- [ ] **Step 3: 커밋**

```bash
git add infra/k8s/platform/webui-bridge-netpol.yaml
git commit -m "feat(bridge): NetworkPolicy로 bridge 7682를 openwebui pipelines만 허용"
```

---

## Task 7: Open WebUI user_pod_pipe 작성

**Files:**
- Create: `infra/k8s/openwebui/user-pod-pipe.py`

- [ ] **Step 1: Pipe 소스 작성**

```python
"""
Open WebUI pipe — 사용자 Pod의 Claude Code bridge로 채팅 위임.

Pipe ID: "user_pod_pipe"
모델 선택: user_pod_pipe.us.anthropic.claude-sonnet-4-6

동작:
  1. __user__["email"] 에서 사번 추출 (local-part before @)
  2. auth-gateway /api/v1/sessions/ensure 호출 (Pod 없으면 생성)
  3. http://<pod_name>.claude-sessions.svc.cluster.local:7682/webui/chat POST
  4. SSE 이벤트를 OpenAI-style delta chunks로 변환하여 WebUI에 yield
"""
import json
import os
from typing import AsyncGenerator

import httpx


class Pipe:
    class Valves:
        pass  # Open WebUI UI에 노출할 설정 없음 (Phase 1b-B에서 확장)

    def __init__(self):
        self.type = "manifold"
        self.id = "user_pod_pipe"
        self.name = "User Pod: "
        self.valves = self.Valves()
        self.AUTH_GATEWAY_URL = os.environ.get(
            "AUTH_GATEWAY_URL",
            "http://auth-gateway.platform.svc.cluster.local",
        )
        self.BRIDGE_SECRET = os.environ["WEBUI_BRIDGE_SECRET"]

    def pipes(self) -> list[dict]:
        # Phase 1a와 동일한 모델 화이트리스트
        return [
            {"id": "us.anthropic.claude-sonnet-4-6", "name": "Sonnet 4.6 (Pod)"},
            {"id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "name": "Haiku 4.5 (Pod)"},
        ]

    def _extract_username(self, user: dict) -> str:
        email = user.get("email", "")
        if "@" in email:
            return email.split("@", 1)[0]
        return user.get("name", "").upper()

    async def _ensure_pod(self, username: str) -> str:
        """auth-gateway에 Pod 존재 보장 요청, pod_name 반환."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{self.AUTH_GATEWAY_URL}/api/v1/sessions/ensure",
                json={"username": username},
                headers={"X-SKO-User-Id": username},
            )
            r.raise_for_status()
            return r.json()["pod_name"]

    async def pipe(self, body: dict, __user__: dict) -> AsyncGenerator[str, None]:
        username = self._extract_username(__user__)
        pod_name = await self._ensure_pod(username)

        model_id = body["model"].split(".", 1)[1]  # "user_pod_pipe.<model>" → <model>
        bridge_url = (
            f"http://{pod_name}.claude-sessions.svc.cluster.local:7682/webui/chat"
        )

        req = {
            "session_id": body.get("chat_id") or body.get("id", "unknown"),
            "messages": body.get("messages", []),
            "model": model_id,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                bridge_url,
                json=req,
                headers={
                    "Authorization": f"Bearer {self.BRIDGE_SECRET}",
                    "X-SKO-User-Id": username,
                },
            ) as stream:
                async for line in stream.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ")
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except Exception:
                        continue
                    # Agent SDK 이벤트에서 text delta만 추출하여 OpenAI-style chunk로 방출
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield delta.get("text", "")
```

- [ ] **Step 2: 커밋**

```bash
git add infra/k8s/openwebui/user-pod-pipe.py
git commit -m "feat(owui): user_pod_pipe — 사용자 Pod bridge 라우팅 (Arch-B MVP)"
```

---

## Task 8: openwebui-pipelines ConfigMap 등록

**Files:**
- Modify: `infra/k8s/openwebui/openwebui-pipelines.yaml`

- [ ] **Step 1: ConfigMap에 user_pod_pipe.py 항목 추가**

기존 `data:` 섹션 하단(`bedrock_ag_pipe.py: |` 바로 뒤)에 동일 패턴으로 추가:

```yaml
  user_pod_pipe.py: |
    # (이 파일 전체는 infra/k8s/openwebui/user-pod-pipe.py의 복사본)
    # 운영 갱신 시 `scripts/sync-owui-pipes.sh`로 자동 동기화 권장 (후속 작업)
```

**운영 편의**: 별도 `scripts/sync-owui-pipes.sh`를 Task 9에서 작성. 지금은 수동 복붙 가능.

- [ ] **Step 2: Pipelines deployment 환경변수에 BRIDGE_SECRET 추가**

같은 파일의 Pipelines Deployment spec envFrom/env에 추가:

```yaml
        env:
        - name: WEBUI_BRIDGE_SECRET
          valueFrom:
            secretKeyRef:
              name: webui-bridge-secret
              key: secret
        - name: AUTH_GATEWAY_URL
          value: "http://auth-gateway.platform.svc.cluster.local"
```

- [ ] **Step 3: 커밋**

```bash
git add infra/k8s/openwebui/openwebui-pipelines.yaml
git commit -m "feat(owui): user_pod_pipe ConfigMap 등록 + BRIDGE_SECRET env"
```

---

## Task 9: auth-gateway /api/v1/sessions/ensure 엔드포인트

**Files:**
- Modify: `auth-gateway/app/routers/sessions.py`

- [ ] **Step 1: 기존 POST /sessions/를 ensure로 재사용 가능한지 확인**

```bash
grep -nE "@router.post|ensure" /Users/cation98/Project/bedrock-ai-agent/auth-gateway/app/routers/sessions.py | head -10
```

- [ ] **Step 2: 신규 엔드포인트 추가 (idempotent — 있으면 기존 Pod 이름 반환, 없으면 생성)**

```python
@router.post("/ensure")
async def ensure_session(
    request: Request,
    body: dict,
    db: Session = Depends(get_db),
):
    """WebUI pipe용 idempotent Pod ensure.

    이미 Pod가 있으면 기존 pod_name 반환, 없으면 create_pod.
    기존 POST / 엔드포인트와 달리 JWT cookie 대신 X-SKO-User-Id 헤더로 사용자 확인
    (cluster-internal 호출만 허용 — NetworkPolicy로 보호).
    """
    username = request.headers.get("X-SKO-User-Id") or body.get("username")
    if not username:
        raise HTTPException(status_code=400, detail="username required")

    # 기존 Pod 검색
    session = db.query(ClaudeSession).filter_by(username=username, status="running").first()
    if session:
        return {"pod_name": session.pod_name, "status": "existing"}

    # 없으면 생성 (기존 POST / 로직 재사용)
    pod_name, proxy_secret, pod_token_hash = k8s.create_pod(username, "default")
    new_session = ClaudeSession(
        username=username, pod_name=pod_name, status="running",
    )
    db.add(new_session)
    db.commit()
    return {"pod_name": pod_name, "status": "created"}
```

(실제 import + 기존 ClaudeSession 모델 구조에 맞게 조정 — 파일 상단 import 확인 후 적용)

- [ ] **Step 3: NetworkPolicy로 /sessions/ensure 를 openwebui namespace만 허용**

`auth-gateway/app/main.py` 또는 기존 NP 재확인: ensure 엔드포인트는 cluster-internal만 허용. 외부 ingress는 이 path 차단.

- [ ] **Step 4: 커밋**

```bash
git add auth-gateway/app/routers/sessions.py infra/k8s/platform/network-policy.yaml
git commit -m "feat(sessions): /sessions/ensure idempotent 엔드포인트 (owui pipe 호출용)"
```

---

## Task 10: 이미지 빌드 + 배포

- [ ] **Step 1: 컨테이너 이미지 빌드·푸시**

```bash
ECR=680877507363.dkr.ecr.ap-northeast-2.amazonaws.com
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin "$ECR"
cd /Users/cation98/Project/bedrock-ai-agent/container-image
docker build --platform linux/amd64 -t "$ECR/bedrock-claude/claude-code-terminal:latest" .
docker push "$ECR/bedrock-claude/claude-code-terminal:latest"
cd /Users/cation98/Project/bedrock-ai-agent/auth-gateway
docker build --platform linux/amd64 -t "$ECR/bedrock-claude/auth-gateway:latest" .
docker push "$ECR/bedrock-claude/auth-gateway:latest"
```

- [ ] **Step 2: K8s 적용**

```bash
kubectl apply -f infra/k8s/openwebui/openwebui-pipelines.yaml
kubectl apply -f infra/k8s/platform/webui-bridge-netpol.yaml
kubectl rollout restart deploy/auth-gateway -n platform
kubectl rollout restart deploy/open-webui-pipelines -n openwebui
kubectl rollout status deploy/auth-gateway -n platform
kubectl rollout status deploy/open-webui-pipelines -n openwebui
```

- [ ] **Step 3: 신규 사용자 Pod 생성 검증** (기존 Pod는 재생성 필요 — bridge 부재)

```bash
# 사용자 로그아웃 후 재로그인 또는 Pod 강제 재시작
kubectl delete pod -n claude-sessions -l user=TESTUSER01 --ignore-not-found
# 로그인 재시도 후
kubectl get pods -n claude-sessions -l user=TESTUSER01
# Bridge 프로세스 확인
POD=$(kubectl get pod -n claude-sessions -l user=TESTUSER01 -o jsonpath='{.items[0].metadata.name}')
kubectl logs -n claude-sessions "$POD" | grep webui-bridge
```

Expected: `webui-bridge started on port 7682 (owner=TESTUSER01)`

---

## Task 11: E2E — WebUI 메시지 → Pod 응답

- [ ] **Step 1: bridge 내부 health check**

```bash
POD=$(kubectl get pod -n claude-sessions -l user=TESTUSER01 -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n claude-sessions "$POD" -- curl -s http://localhost:7682/health
```

Expected: `{"status":"ok","owner":"TESTUSER01"}`

- [ ] **Step 2: bridge 직접 호출 (secret 주입)**

```bash
SECRET=$(kubectl get secret webui-bridge-secret -n claude-sessions -o jsonpath='{.data.secret}' | base64 -d)
kubectl exec -n claude-sessions "$POD" -- curl -s -X POST \
  -H "Authorization: Bearer $SECRET" \
  -H "X-SKO-User-Id: TESTUSER01" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test","messages":[{"role":"user","content":"Say hello in one word"}],"model":"us.anthropic.claude-sonnet-4-6"}' \
  http://localhost:7682/webui/chat | head -20
```

Expected: SSE 이벤트 여러 줄, 최소 1개 text_delta 포함

- [ ] **Step 3: WebUI 브라우저 E2E**

1. `ai-chat.skons.net` 접속 (TESTUSER01 계정)
2. 모델 드롭다운에 "Sonnet 4.6 (Pod)" / "Haiku 4.5 (Pod)" 신규 2개 노출 확인
3. "Sonnet 4.6 (Pod)" 선택 → "hello"
4. 스트리밍 응답 수신 확인
5. `kubectl logs` 로 bridge 요청 로그 확인

---

## Task 12: 스킬 양방향 호환성 검증

- [ ] **Step 1: 터미널에서 테스트 스킬 생성**

Pod 터미널(ttyd)에 SSH 또는 WebUI 아닌 claude.skons.net 터미널 접속 후:
```bash
mkdir -p ~/.claude/commands
cat > ~/.claude/commands/test-echo.md <<'EOF'
---
description: 테스트용 에코 명령 — 인자를 그대로 반환
---
인자를 그대로 출력하라. 다른 작업은 하지 마라.
EOF
```

- [ ] **Step 2: 터미널에서 /test-echo hello 실행 → 응답 확인**

- [ ] **Step 3: WebUI(Sonnet 4.6 Pod)에서 "test-echo 스킬로 hello 출력해줘" 요청**

자연어 위임으로 bridge Claude가 `~/.claude/commands/test-echo.md`를 발견·실행해야 함.

Expected: 양쪽에서 동일 응답. 스펙 §7.4 X1 (자동 발견) 증명.

- [ ] **Step 4: 메모리 공유 검증**

터미널에서:
```
/memory write "오늘 점심은 칼국수"
```

WebUI에서: "내 메모리에 뭐가 있어?"

Expected: WebUI가 동일 메모리 참조 (파일시스템 공유).

- [ ] **Step 5: 결과 문서화**

`docs/qa/phase1b-a-skill-compat-results.md`에 테스트 결과 기록 (PASS/FAIL + 스크린샷).

```bash
git add docs/qa/phase1b-a-skill-compat-results.md
git commit -m "docs(qa): Phase 1b-A 스킬 양방향 호환성 검증 결과"
```

---

## Acceptance Criteria

- [ ] Task 2 단위 테스트 4/4 PASS
- [ ] 신규 사용자 Pod에 `webui-bridge` 프로세스 기동 확인 (로그)
- [ ] `/webui/chat` 엔드포인트가 SSE 응답 반환 (kubectl exec curl)
- [ ] Open WebUI 모델 드롭다운에 "Sonnet 4.6 (Pod)" 노출
- [ ] WebUI에서 보낸 메시지 → Pod 내부 Claude 실행 → 응답 스트리밍 완료
- [ ] 터미널에서 만든 스킬이 WebUI에서도 동작 (X1 자동 발견 증명)
- [ ] 메모리 파일 공유 검증 (L2 파일시스템 공유 증명)

## Rollback

- Pipe만 되돌리기: 이전 `bedrock_ag_pipe` 로 default model 전환 (ConfigMap revert)
- Bridge 오류: Pod 재시작 시 bridge 비활성화 (WEBUI_BRIDGE_SECRET env 제거)
- 최악: deploy undo — `kubectl rollout undo` 로 이전 image로

## 미해결 (Phase 1b-B/1b-C로 이관)

- Tier 1 로깅 (bridge 요청/응답 S3 기록) — 1b-B
- 슬래시 명령 forwarding (X2) — 1b-B 이후
- Pod 콜드스타트 UX ("터미널 준비 중...") — 1b-C
- 사용자 통지 + ToS — 1b-C
