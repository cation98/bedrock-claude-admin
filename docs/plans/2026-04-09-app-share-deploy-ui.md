# 웹앱 공유하기 버튼 + 배포 UI 구현 계획

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Hub의 로컬 웹앱에 "공유하기" 버튼을 추가하고, 공유 범위(ACL) 선택 모달을 통해 앱을 팀/회사에 배포할 수 있게 한다.

**Architecture:** 기존 구현(deploy API, ACL 시스템, webapp-login, auth-check)을 Hub UI에 연결. 새 엔드포인트 없이 기존 `POST /api/v1/apps/deploy`와 ACL 모달을 활용. 배포 시 앱 코드를 EFS deployed 경로에 복사하고, app_deploy_service가 K8s Pod/Service/Ingress를 생성.

**Tech Stack:** JavaScript (Hub UI), FastAPI (deploy endpoint), K8s (app_deploy_service.py)

---

## 기존 구현 현황

| 컴포넌트 | 상태 | 위치 |
|----------|------|------|
| `POST /api/v1/apps/deploy` | ✅ 구현됨 | `auth-gateway/app/routers/apps.py:572` |
| 5종 ACL (user/team/region/job/company) | ✅ 구현됨 | `apps.py:769-882` |
| auth-check (Ingress ACL 검증) | ✅ 구현됨 | `apps.py:135-269` |
| webapp-login (SSO + 2FA) | ✅ 구현됨 | `auth-gateway/app/static/webapp-login.html` |
| app_deploy_service (K8s 리소스) | ✅ 구현됨 | `auth-gateway/app/services/app_deploy_service.py` |
| ACL 모달 UI | ✅ 구현됨 | `fileserver.py` JS (openAclModal, searchUsers) |
| **"공유하기" 버튼** | ❌ 없음 | `fileserver.py` buildUnifiedAppItem |
| **배포 모달 UI** | ❌ 없음 | — |
| **deploy → K8s 연결** | ⚠️ 확인 필요 | apps.py deploy endpoint |

---

## Task 1: deploy 엔드포인트에서 K8s 배포 호출 확인

**Files:**
- Read: `auth-gateway/app/routers/apps.py:572-669`
- Read: `auth-gateway/app/services/app_deploy_service.py:449-592`

**Step 1: deploy 엔드포인트가 app_deploy_service를 호출하는지 확인**

apps.py의 `deploy_app` 함수를 읽고, `app_deploy_service.deploy_app()`이 호출되는지 확인.
호출되지 않으면 추가해야 함.

**Step 2: 앱 코드 복사 메커니즘 확인**

deploy service의 Pod이 EFS `users/{username}/deployed/{app_name}/`에서 코드를 읽는지 확인.
현재는 `users/{username}/{app_name}/`(workspace)에 코드가 있으므로, 배포 시 복사가 필요할 수 있음.

**Step 3: 필요시 deploy 엔드포인트 수정**

deploy 엔드포인트에서:
1. EFS 코드 경로 → deployed 경로로 복사 (or 심볼릭 링크)
2. app_deploy_service.deploy_app() 호출
3. K8s Pod/Service/Ingress 생성 확인

**Step 4: 커밋**
```bash
git commit -m "feat: deploy 엔드포인트에서 K8s 배포 연결"
```

---

## Task 2: Hub UI — "공유하기" 버튼 추가

**Files:**
- Modify: `container-image/fileserver.py` — buildUnifiedAppItem JS 함수

**Step 1: 'stopped' 상태에 "공유하기" 버튼 추가**

현재 (line ~2882):
```javascript
} else if (status === 'stopped') {
    // 실행, 삭제 버튼만 있음
}
```

변경 — "실행" 옆에 "공유하기" 버튼 추가:
```javascript
} else if (status === 'stopped') {
    var startBtn = ...; // 기존 실행 버튼
    actions.appendChild(startBtn);
    var shareBtn = document.createElement('button'); shareBtn.className = 'btn-sm';
    shareBtn.style.borderColor = '#a371f7'; shareBtn.style.color = '#a371f7';
    shareBtn.textContent = '공유하기';
    shareBtn.onclick = function() { openDeployModal(app.name, app.path); };
    actions.appendChild(shareBtn);
    var delProjBtn = ...; // 기존 삭제 버튼
}
```

**Step 2: 'running' 상태에도 "공유하기" 버튼 추가**

현재:
```javascript
} else if (status === 'running') {
    // 열기, 중지 버튼만 있음
}
```

변경 — "중지" 앞에 "공유하기" 버튼 추가:
```javascript
} else if (status === 'running') {
    var openBtn = ...; // 기존 열기 버튼
    actions.appendChild(openBtn);
    var shareBtn = document.createElement('button'); shareBtn.className = 'btn-sm';
    shareBtn.style.borderColor = '#a371f7'; shareBtn.style.color = '#a371f7';
    shareBtn.textContent = '공유하기';
    shareBtn.onclick = function() { openDeployModal(app.name, app.path); };
    actions.appendChild(shareBtn);
    var stopBtn = ...; // 기존 중지 버튼
}
```

**Step 3: 커밋**
```bash
git commit -m "feat: Hub 앱 목록에 공유하기 버튼 추가"
```

---

## Task 3: 배포 모달 UI 구현

**Files:**
- Modify: `container-image/fileserver.py` — PORTAL_TEMPLATE에 모달 HTML + JS 추가

**Step 1: 배포 모달 HTML 추가**

기존 ACL 모달 (`<div id="aclModal">`) 근처에 배포 모달 추가:

```html
<!-- 배포 모달 -->
<div class="modal-overlay" id="deployModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3>웹앱 공유하기</h3>
      <button class="close-btn" onclick="closeDeployModal()">&times;</button>
    </div>
    <div style="padding:16px;">
      <div style="margin-bottom:16px;">
        <div style="font-size:0.82rem;color:#8b949e;margin-bottom:4px;">앱 이름</div>
        <div id="deployAppName" style="font-weight:bold;font-size:1rem;"></div>
      </div>
      
      <div style="margin-bottom:16px;">
        <div style="font-size:0.82rem;color:#8b949e;margin-bottom:8px;">공개 범위</div>
        <label style="display:block;margin-bottom:6px;cursor:pointer;">
          <input type="radio" name="deployVisibility" value="private" checked> 
          비공개 (허용된 사용자만)
        </label>
        <label style="display:block;cursor:pointer;">
          <input type="radio" name="deployVisibility" value="company"> 
          전사 공개 (모든 임직원)
        </label>
      </div>
      
      <div id="deployAclSection" style="margin-bottom:16px;">
        <div style="font-size:0.82rem;color:#8b949e;margin-bottom:8px;">접근 허용 사용자</div>
        <div style="display:flex;gap:8px;margin-bottom:8px;">
          <input type="text" id="deployUserSearch" placeholder="사번 또는 이름 검색..."
                 style="flex:1;padding:7px 10px;background:#161b22;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:0.82rem;outline:none;"
                 onkeypress="if(event.key==='Enter')searchDeployUsers()">
          <button onclick="searchDeployUsers()" style="padding:7px 14px;background:#21262d;border:1px solid #30363d;border-radius:6px;color:#58a6ff;font-size:0.82rem;cursor:pointer;">검색</button>
        </div>
        <ul class="acl-list" id="deploySearchResults" style="max-height:120px;overflow-y:auto;"></ul>
        <div style="font-size:0.78rem;color:#8b949e;margin-top:8px;">선택된 사용자:</div>
        <ul class="acl-list" id="deploySelectedUsers" style="max-height:100px;overflow-y:auto;"></ul>
      </div>
      
      <button onclick="executeDeploy()" style="width:100%;padding:10px;background:#238636;border:none;border-radius:8px;color:#fff;font-size:0.9rem;font-weight:bold;cursor:pointer;">
        배포하기
      </button>
    </div>
  </div>
</div>
```

**Step 2: 배포 모달 JS 함수 구현**

```javascript
var deployAppNameVal = '';
var deployAppPathVal = '';
var deploySelectedUsernames = [];

function openDeployModal(appName, appPath) {
  deployAppNameVal = appName;
  deployAppPathVal = appPath;
  deploySelectedUsernames = [];
  document.getElementById('deployAppName').textContent = appName;
  document.getElementById('deployUserSearch').value = '';
  document.getElementById('deploySearchResults').replaceChildren();
  document.getElementById('deploySelectedUsers').replaceChildren();
  document.querySelector('input[name="deployVisibility"][value="private"]').checked = true;
  document.getElementById('deployModal').classList.add('active');
}

function closeDeployModal() {
  document.getElementById('deployModal').classList.remove('active');
}

function searchDeployUsers() {
  var q = document.getElementById('deployUserSearch').value.trim();
  if (!q) return;
  apiFetch('/files/org-members?q=' + encodeURIComponent(q)).then(function(data) {
    var users = data.members || [];
    var el = document.getElementById('deploySearchResults');
    el.replaceChildren();
    users.forEach(function(u) {
      var li = document.createElement('li'); li.className = 'acl-item';
      var info = document.createElement('span'); info.className = 'user-info';
      info.textContent = (u.name || u.username) + ' (' + u.username + ') ' + (u.team_name || '');
      li.appendChild(info);
      var btn = document.createElement('button'); btn.className = 'btn-sm';
      btn.style.borderColor = '#238636'; btn.style.color = '#3fb950';
      btn.textContent = '추가';
      btn.onclick = function() {
        if (deploySelectedUsernames.indexOf(u.username) === -1) {
          deploySelectedUsernames.push(u.username);
          renderDeploySelected();
        }
      };
      li.appendChild(btn);
      el.appendChild(li);
    });
  });
}

function renderDeploySelected() {
  var el = document.getElementById('deploySelectedUsers');
  el.replaceChildren();
  deploySelectedUsernames.forEach(function(uname, i) {
    var li = document.createElement('li'); li.className = 'acl-item';
    var info = document.createElement('span'); info.textContent = uname;
    li.appendChild(info);
    var btn = document.createElement('button'); btn.className = 'btn-sm danger';
    btn.textContent = '제거';
    btn.onclick = function() {
      deploySelectedUsernames.splice(i, 1);
      renderDeploySelected();
    };
    li.appendChild(btn);
    el.appendChild(li);
  });
}

function executeDeploy() {
  var visibility = document.querySelector('input[name="deployVisibility"]:checked').value;
  var t = document.getElementById('hubToast');
  t.textContent = '배포 중: ' + deployAppNameVal;
  t.style.display = 'block';
  
  apiFetch('/apps/deploy', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      app_name: deployAppNameVal,
      visibility: visibility,
      app_port: 3000,
      acl_usernames: visibility === 'company' ? [] : deploySelectedUsernames
    })
  }).then(function(data) {
    closeDeployModal();
    if (data.app_url) {
      t.textContent = '배포 완료! ' + data.app_url;
    } else if (data.detail) {
      t.textContent = '배포 실패: ' + data.detail;
      t.style.background = '#da3633';
    } else {
      t.textContent = '배포 완료!';
    }
    setTimeout(function() { t.style.display = 'none'; t.style.background = ''; }, 4000);
    loadMyApps();
  }).catch(function(err) {
    t.textContent = '배포 오류: ' + (err.message || err);
    t.style.background = '#da3633';
    setTimeout(function() { t.style.display = 'none'; t.style.background = ''; }, 5000);
  });
}
```

**Step 3: visibility 라디오 변경 시 ACL 섹션 표시/숨김**

```javascript
// company 선택 시 ACL 섹션 숨김
document.querySelectorAll('input[name="deployVisibility"]').forEach(function(radio) {
  radio.onchange = function() {
    document.getElementById('deployAclSection').style.display = 
      this.value === 'company' ? 'none' : 'block';
  };
});
```

**Step 4: 커밋**
```bash
git commit -m "feat: 웹앱 공유하기 배포 모달 UI 구현"
```

---

## Task 4: 배포 엔드포인트에 K8s 배포 트리거 추가

**Files:**
- Modify: `auth-gateway/app/routers/apps.py:572-669`

**Step 1: deploy 엔드포인트에서 app_deploy_service 호출**

현재 deploy 엔드포인트는 DB 레코드만 생성. K8s 리소스 생성을 추가:

```python
# apps.py deploy_app 함수 끝에 추가
from app.services.app_deploy_service import AppDeployService

# DB 레코드 생성 후:
try:
    deploy_service = AppDeployService(settings)
    deploy_service.deploy_app(
        owner_username=username,
        app_name=request.app_name,
        version=version,
        app_port=request.app_port,
    )
except Exception as e:
    logger.error(f"K8s deploy failed for {request.app_name}: {e}")
    # DB 레코드는 유지 (status를 inactive로 변경)
    app.status = "inactive"
    db.commit()
```

**Step 2: 커밋**
```bash
git commit -m "feat: deploy 엔드포인트에 K8s 리소스 생성 트리거 추가"
```

---

## Task 5: 컨테이너 이미지 + auth-gateway 빌드 & 배포

**Step 1: container-image 빌드 & 푸시**
```bash
cd container-image
docker build --platform linux/amd64 -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest .
docker push 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest
```

**Step 2: auth-gateway 빌드 & 배포**
```bash
cd auth-gateway
docker build --platform linux/amd64 -t 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest .
docker push 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/auth-gateway:latest
kubectl rollout restart deployment/auth-gateway -n platform
```

**Step 3: 검증**
- 재로그인 후 Hub에서 로컬 앱에 "공유하기" 버튼 확인
- 버튼 클릭 → 모달 열림 → 범위 선택 → 사용자 검색/추가 → "배포하기" 클릭
- 배포 후 `/apps/{username}/{app_name}/` URL 접근 확인
- 다른 사용자로 접근 시 webapp-login 표시 확인

---

## 데이터 흐름 (완성 후)

```
사용자가 Hub에서 로컬 앱 "공유하기" 클릭
  → 배포 모달 열림 (공개 범위 + ACL 사용자 선택)
  → "배포하기" 클릭
  → POST /api/v1/apps/deploy
    → DB: deployed_apps 레코드 생성
    → DB: app_acl 레코드 생성 (선택한 사용자)
    → K8s: Pod + Service + Ingress 생성 (claude-apps 네임스페이스)
  → 배포 완료 토스트 + URL 표시

공유받은 사용자가 URL 접근
  → Ingress → auth-check → ACL 검증 통과 → 앱 표시

미인가 사용자가 URL 접근
  → auth-check → 401 → webapp-login 리다이렉트
  → 로그인 후 → auth-check → 403 (ACL 미등록)
```
