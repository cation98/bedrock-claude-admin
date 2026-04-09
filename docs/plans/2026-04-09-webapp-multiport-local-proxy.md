# 웹앱 다중 포트 로컬 프록시 구현 계획

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 각 사용자 앱이 독립 포트(3000-3100)에서 실행되고, Hub에서 실행/열기/종료가 정상 동작하며, 본인만 접근 가능하도록 한다.

**Architecture:** fileserver(8080)를 로컬 리버스 프록시로 활용. `/webapp/{port}/` 경로로 요청 → `localhost:{port}` 로 프록시. Ingress/Service 변경 없이 기존 `/files/` 인증 체계를 그대로 사용. `/app/{pod}/` 경로의 하드코딩 3000 문제를 우회.

**Tech Stack:** Python http.server (fileserver.py), httpx (Pod 내부 프록시), JavaScript (Hub UI)

---

## 현재 문제 분석

### 근본 원인
```
[Ingress] /app/{pod_name}/ → Pod:3000 (하드코딩, k8s_service.py:555)
[Service] port: 3000만 노출 (k8s_service.py:503)
[app_proxy.py] _port 파라미터 처리 코드 존재하지만, 요청이 여기까지 도달하지 않음
```

### 왜 auth-gateway의 app_proxy.py가 호출되지 않나?
1. per-pod Ingress: `/app/{pod_name}(/|$)(.*)` → Pod:3000 (더 구체적인 경로)
2. platform Ingress: `/` → auth-gateway (catch-all)
3. Nginx는 더 구체적인 경로를 우선 매칭 → per-pod Ingress가 승리 → auth-gateway 무시

### 해결 전략: fileserver를 리버스 프록시로 활용
- fileserver(8080)는 Pod 내부에서 localhost로 동작
- `/files/{pod_name}/` 경로는 이미 auth-url 보호 (Pod 소유자만 접근 가능)
- fileserver에 `/webapp/{port}/*` 프록시 핸들러를 추가하면:
  - 인증: 기존 files auth-check 그대로 사용 ✓
  - 다중 포트: localhost:{port}로 직접 프록시 ✓
  - 소유자 전용: `/files/` auth-url이 소유자 검증 ✓
  - K8s 변경 없음 ✓

---

## Task 1: fileserver.py에 리버스 프록시 핸들러 추가

**Files:**
- Modify: `container-image/fileserver.py` — do_GET, do_POST에 `/webapp/` 경로 핸들러 추가

**Step 1: do_GET에 webapp 프록시 라우팅 추가**

`fileserver.py`의 `do_GET` 메서드에서 `/webapp/{port}/` 경로를 감지하여 프록시 핸들러로 라우팅:

```python
# do_GET 상단에 추가 (기존 API 라우팅 블록 안)
if parsed.path.startswith('/webapp/'):
    self._handle_webapp_proxy(parsed)
    return
```

**Step 2: do_POST에도 동일 라우팅 추가**

```python
# do_POST 상단에 추가 (기존 API 라우팅 블록 안)
if parsed.path.startswith('/webapp/'):
    self._handle_webapp_proxy(parsed)
    return
```

**Step 3: _handle_webapp_proxy 핸들러 구현**

```python
def _handle_webapp_proxy(self, parsed):
    """GET/POST /webapp/{port}/* → localhost:{port}/* 리버스 프록시."""
    import http.client
    
    parts = parsed.path.split('/', 3)  # ['', 'webapp', '{port}', '{path}']
    if len(parts) < 3 or not parts[2].isdigit():
        self._send_json(400, {"error": "잘못된 웹앱 경로입니다. /webapp/{port}/ 형식을 사용하세요."})
        return
    
    port = int(parts[2])
    if not (3000 <= port <= 3100):
        self._send_json(400, {"error": "포트는 3000-3100 범위만 허용됩니다."})
        return
    
    # 프록시 대상 경로 구성
    target_path = '/' + parts[3] if len(parts) > 3 else '/'
    if parsed.query:
        target_path += '?' + parsed.query
    
    # 요청 본문 읽기 (POST 등)
    content_length = int(self.headers.get('Content-Length', 0))
    body = self.rfile.read(content_length) if content_length > 0 else None
    
    # 헤더 복사 (Host 제거, 프록시 대상 설정)
    headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'connection')}
    headers['Host'] = f'localhost:{port}'
    
    try:
        conn = http.client.HTTPConnection('localhost', port, timeout=30)
        conn.request(self.command, target_path, body=body, headers=headers)
        resp = conn.getresponse()
        
        # 응답 전달
        self.send_response(resp.status)
        for key, val in resp.getheaders():
            if key.lower() not in ('transfer-encoding', 'connection'):
                self.send_header(key, val)
        self.end_headers()
        
        # 본문 스트리밍 (청크 단위)
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            self.wfile.write(chunk)
        
        conn.close()
    except ConnectionRefusedError:
        self._send_json(503, {"error": f"포트 {port}에서 실행 중인 앱이 없습니다."})
    except Exception as e:
        self._send_json(502, {"error": f"프록시 오류: {str(e)}"})
```

**Step 4: 다른 HTTP 메서드(PUT, DELETE, PATCH)도 지원**

```python
def do_PUT(self):
    parsed = urllib.parse.urlparse(self.path)
    if parsed.path.startswith('/webapp/'):
        self._handle_webapp_proxy(parsed)
        return
    self.send_error(405, "Method Not Allowed")

def do_DELETE(self):
    parsed = urllib.parse.urlparse(self.path)
    if parsed.path.startswith('/webapp/'):
        self._handle_webapp_proxy(parsed)
        return
    # 기존 delete 핸들러...

def do_PATCH(self):
    parsed = urllib.parse.urlparse(self.path)
    if parsed.path.startswith('/webapp/'):
        self._handle_webapp_proxy(parsed)
        return
    self.send_error(405, "Method Not Allowed")
```

**Step 5: 커밋**
```bash
git add container-image/fileserver.py
git commit -m "feat: fileserver에 /webapp/{port}/ 리버스 프록시 추가"
```

---

## Task 2: Hub UI — "열기" URL을 fileserver 프록시 경로로 변경

**Files:**
- Modify: `container-image/fileserver.py` — JavaScript: startApp, loadMyApps, buildUnifiedAppItem

**Step 1: startApp 함수 — 새 탭 URL 변경**

기존:
```javascript
var appUrl = '/app/' + hostname + '/';
if (data.port && data.port !== 3000) appUrl += '?_port=' + data.port;
```

변경:
```javascript
var appUrl = fileserverBase + '/webapp/' + data.port + '/';
```

모든 포트를 동일한 패턴으로 처리. `/files/{pod_name}/webapp/{port}/`

**Step 2: loadMyApps render — app_url 생성 변경**

기존:
```javascript
var appUrl = '/app/' + hostname + '/';
if (a.port && a.port !== 3000) appUrl = '/app/' + hostname + '/?_port=' + a.port;
```

변경:
```javascript
var appUrl = fileserverBase + '/webapp/' + a.port + '/';
```

**Step 3: buildUnifiedAppItem — "열기" 버튼이 올바른 URL 사용하는지 확인**

기존 코드(이미 `app.app_url`을 사용):
```javascript
openBtn.href = app.app_url || '#';
```

app_url이 올바르게 전달되므로 변경 불필요.

**Step 4: 커밋**
```bash
git add container-image/fileserver.py
git commit -m "feat: 열기 URL을 /files/{pod}/webapp/{port}/ 프록시 경로로 변경"
```

---

## Task 3: 앱 시작 — 포트 할당 로직 단순화 및 검증

**Files:**
- Modify: `container-image/fileserver.py` — _handle_apps_start

**Step 1: 포트 할당 로직 정리**

현재 문제: registry에 이전 포트가 남아있어 혼란 유발.

변경 원칙:
- 미실행 앱의 registry port는 항상 `null` (status API에서 이미 자동 리셋)
- 시작 시: 리스닝 포트 + 다른 앱의 registry 포트를 제외하고 빈 포트 할당
- 동일 앱 재시작: 기존 프로세스 종료 → 같은 포트 재사용

```python
def _handle_apps_start(self):
    body = self._read_body()
    data = json.loads(body)
    app_name = data.get('name') or os.path.basename(data.get('path', ''))
    if not app_name:
        self._send_json(400, {"error": "앱 이름 또는 경로가 필요합니다"})
        return
    app_path = data.get('path', os.path.join(self.directory, app_name))
    app_type = data.get('type', 'python')

    # Safety check
    real_app_path = os.path.realpath(app_path)
    real_workspace = os.path.realpath(self.directory)
    if not real_app_path.startswith(real_workspace + os.sep):
        self._send_json(403, {"error": "workspace 외부 경로에서는 실행할 수 없습니다"})
        return

    # 레지스트리 로드
    reg_path = os.path.join(self.directory, '.webapp-registry.json')
    registry = {}
    if os.path.exists(reg_path):
        with open(reg_path) as f:
            registry = json.load(f)

    # 현재 리스닝 포트 + CWD 매핑 조회
    port_map = self._scan_listening_ports()
    listening_ports = set(port_map.keys())
    cwd_to_port = {v['cwd']: v['port'] for v in port_map.values() if v.get('cwd')}

    # 이 앱이 이미 실행 중이면 → 기존 프로세스 종료, 같은 포트 재사용
    existing_running_port = cwd_to_port.get(app_path)
    if existing_running_port:
        self._kill_port(existing_running_port)
        port = existing_running_port
    else:
        # 사용 중인 포트 = 리스닝 포트 (정확한 진실의 원천)
        used = set(listening_ports)
        port = None
        for p in range(3000, 3101):
            if p not in used:
                port = p
                break
        if port is None:
            self._send_json(503, {"error": "사용 가능한 포트가 없습니다"})
            return

    # entrypoint 결정
    entrypoint = registry.get(app_name, {}).get('entrypoint')
    if not entrypoint:
        entrypoint = 'main:app' if os.path.isfile(os.path.join(app_path, 'main.py')) else 'app:app'

    # 명령어 구성
    env = os.environ.copy()
    env['PORT'] = str(port)
    if app_type == 'node':
        cmd = ['npm', 'start']
        pkg_json = os.path.join(app_path, 'package.json')
        if os.path.exists(pkg_json):
            with open(pkg_json) as f:
                pkg = json.load(f)
            if 'dev' in pkg.get('scripts', {}):
                cmd = ['npm', 'run', 'dev']
    elif app_type == 'python':
        cmd = ['python3', '-m', 'uvicorn', entrypoint, '--host', '0.0.0.0', '--port', str(port)]
    else:
        self._send_json(400, {"error": f"지원하지 않는 앱 유형: {app_type}"})
        return

    # 레지스트리 업데이트 (프로세스 시작 전)
    old = registry.get(app_name, {})
    registry[app_name] = {
        "port": port, "path": app_path, "type": app_type,
        "entrypoint": entrypoint if app_type == 'python' else old.get('entrypoint'),
        "auto_detected": old.get('auto_detected', False)
    }
    with open(reg_path, 'w') as f:
        json.dump(registry, f, indent=2, default=str)

    # 프로세스 시작
    subprocess.Popen(cmd, cwd=app_path, env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    self._send_json(200, {"started": True, "name": app_name, "port": port})
```

**Step 2: 커밋**
```bash
git add container-image/fileserver.py
git commit -m "fix: 포트 할당 — 리스닝 포트만 진실의 원천으로 사용"
```

---

## Task 4: 컨테이너 이미지 빌드 & 배포

**Step 1: 이미지 빌드**
```bash
cd container-image
docker build --platform linux/amd64 -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest .
```

**Step 2: ECR 푸시**
```bash
docker push 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest
```

**Step 3: 검증 — Pod에서 리버스 프록시 테스트**
```bash
# Pod에서 앱 시작
kubectl exec -n claude-sessions {pod} -- curl -s -X POST http://localhost:8080/api/apps/start \
  -H 'Content-Type: application/json' \
  -d '{"path":"/home/node/workspace/tbm-dashboard","type":"python"}'
# 예상 응답: {"started": true, "name": "tbm-dashboard", "port": 3000}

# 리버스 프록시 테스트
kubectl exec -n claude-sessions {pod} -- curl -s http://localhost:8080/webapp/3000/ | head -5
# 예상: tbm-dashboard의 HTML 출력

# 다른 앱 시작 (3001 할당 예상)
kubectl exec -n claude-sessions {pod} -- curl -s -X POST http://localhost:8080/api/apps/start \
  -H 'Content-Type: application/json' \
  -d '{"path":"/home/node/workspace/alarm_dashboard","type":"python"}'
# 예상 응답: {"started": true, "name": "alarm_dashboard", "port": 3001}

# 두 번째 앱 프록시 테스트
kubectl exec -n claude-sessions {pod} -- curl -s http://localhost:8080/webapp/3001/ | head -5
# 예상: alarm_dashboard의 HTML 출력 (tbm-dashboard와 다른 내용)
```

**Step 4: 커밋**
```bash
git commit -m "chore: 컨테이너 이미지 빌드 & 배포 검증 완료"
```

---

## Task 5: 불필요한 코드 정리

**Files:**
- Modify: `auth-gateway/app/routers/app_proxy.py` — `_port` 파라미터 코드 제거 (사용되지 않음)

**Step 1: app_proxy.py에서 _port 관련 코드 제거**

`_port` 파라미터는 Ingress가 auth-gateway를 거치지 않으므로 불필요. 원래 코드로 복원:

```python
# 제거할 코드:
# port_param = request.query_params.get("_port")
# if port_param and port_param.isdigit() and 3000 <= int(port_param) <= 3100:
#     app_port = int(port_param)
# query_pairs = [(k, v) for k, v in request.query_params.items() if k != "_port"]
```

**Step 2: auth-gateway 빌드 & 배포**
```bash
cd auth-gateway
docker build --platform linux/amd64 -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest .
docker push 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest
kubectl rollout restart deployment/auth-gateway -n platform
```

**Step 3: 커밋**
```bash
git add auth-gateway/app/routers/app_proxy.py
git commit -m "cleanup: app_proxy.py _port 파라미터 제거 (미사용)"
```

---

## 요약: 데이터 흐름 (수정 후)

```
사용자가 Hub에서 "실행" 클릭
  → POST /files/{pod}/api/apps/start → fileserver:8080
  → 빈 포트(3000) 할당 → uvicorn app:app --port 3000 시작
  → 응답: {port: 3000}

사용자가 "열기" 클릭 (또는 실행 후 자동)
  → 새 탭: /files/{pod}/webapp/3000/
  → Ingress → auth-check (소유자 검증) → fileserver:8080
  → fileserver: /webapp/3000/* → localhost:3000/*
  → 앱 HTML 반환

다른 앱 "실행" 클릭
  → 3000 사용 중 → 3001 할당 → 시작
  → 새 탭: /files/{pod}/webapp/3001/
  → fileserver → localhost:3001 → 다른 앱 HTML

"중지" 클릭
  → POST /files/{pod}/api/apps/stop {port: 3001}
  → _kill_port(3001) → SIGTERM
  → 목록 새로고침 → "미실행" 표시
```

## 보안: 소유자 전용 접근

- `/files/{pod}/` 경로는 Ingress auth-url로 보호:
  - `files-auth-check`: JWT 검증 + Pod 소유자 확인
  - 다른 사용자가 URL을 알아도 403 반환
- localhost와 동일한 격리 효과
