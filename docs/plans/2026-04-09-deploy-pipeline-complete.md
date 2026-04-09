# 웹앱 배포 파이프라인 완성 + Username 프라이버시

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 웹앱 공유/배포 파이프라인을 end-to-end 동작하게 하고, URL/리소스명에서 사번(username) 노출을 제거한다.

**Architecture:** (1) 배포 시 앱 코드를 EFS deployed 경로로 복사하는 fileserver API 추가. (2) deploy 엔드포인트의 코드 버그(pod name 불일치, undeploy K8s 미삭제) 수정. (3) username 대신 8자리 해시 slug를 앱 URL/K8s 리소스명에 사용.

**Tech Stack:** FastAPI, K8s Python client, JavaScript, SHA-256 hash

---

## 인프라 현황 (확인 완료)

| 리소스 | 상태 | 비고 |
|--------|------|------|
| `claude-apps` namespace | ✅ Active | 8일 전 생성 |
| `efs-apps-pvc` (50Gi) | ✅ Bound | EFS 마운트 정상 |
| `app-runtime:latest` ECR | ✅ 존재 | Dockerfile: `container-image/app-runtime/` |
| NetworkPolicy | ⚠️ 라벨 불일치 | 별도 수정 필요 (claude-app vs claude-webapp) |

## Username 노출 현황

현재 사번이 그대로 노출되는 경로:
```
/apps/n1102359/dashboard/       ← 배포 앱 URL (사번 노출)
app-n1102359-dashboard          ← K8s Pod/Service/Ingress 이름 (사번 노출)
/hub/claude-terminal-n1102359/  ← 개인 Hub URL (기존, 이번 범위 외)
```

**해결 방안:** 배포 앱에 대해 username 대신 8자리 SHA-256 해시 slug 사용:
```
/apps/a7b3c9e1/dashboard/       ← 해시 기반 URL (사번 비식별)
app-a7b3c9e1-dashboard          ← K8s 리소스명 (사번 비식별)
```

---

## Task 1: Username 프라이버시 — app_slug 생성

**Files:**
- Modify: `auth-gateway/app/models/user.py` — `app_slug` 컬럼 추가
- Create: `auth-gateway/alembic/versions/xxxx_add_app_slug.py` — DB 마이그레이션
- Modify: `auth-gateway/app/routers/sessions.py` — 로그인 시 slug 자동 생성

**구현:**

users 테이블에 `app_slug` 컬럼 추가 (8자리 hex, unique):
```python
# user.py
app_slug = Column(String(16), unique=True, nullable=True)
```

slug 생성 함수:
```python
import hashlib
def generate_app_slug(username: str) -> str:
    return hashlib.sha256(username.encode()).hexdigest()[:8]
```

기존 사용자에게 일괄 slug 부여:
```sql
UPDATE users SET app_slug = LEFT(encode(sha256(username::bytea), 'hex'), 8) 
WHERE app_slug IS NULL;
```

---

## Task 2: app_deploy_service — slug 기반 URL/리소스명

**Files:**
- Modify: `auth-gateway/app/services/app_deploy_service.py:69-78` — `_app_pod_name`, `_app_url`

변경:
```python
@staticmethod
def _app_pod_name(slug: str, app_name: str) -> str:
    safe_app = app_name.lower().replace("_", "-")
    return f"app-{slug}-{safe_app}"

@staticmethod
def _app_url(slug: str, app_name: str) -> str:
    return f"/apps/{slug}/{app_name.lower()}/"
```

Ingress 경로도 slug 기반:
```python
path=f"/apps/{slug}/{app_name.lower()}(/|$)(.*)"
```

---

## Task 3: deploy 엔드포인트 버그 수정

**Files:**
- Modify: `auth-gateway/app/routers/apps.py:572-669`

**수정 사항:**
1. **pod_name 생성**: 인라인 f-string 대신 `AppDeployService._app_pod_name(slug, app_name)` 사용
2. **app_url 생성**: `AppDeployService._app_url(slug, app_name)` 사용
3. **K8s 리소스 생성**: 이미 추가됨 (이전 커밋), slug 파라미터로 변경
4. **실패 시 응답**: status="inactive"면 HTTP 201 대신 HTTP 207 또는 에러 메시지 포함

---

## Task 4: undeploy 엔드포인트 — K8s 리소스 삭제 추가

**Files:**
- Modify: `auth-gateway/app/routers/apps.py` — `DELETE /apps/{app_name}` 엔드포인트

현재 DB soft-delete만 수행. K8s 리소스 삭제 추가:
```python
# undeploy 엔드포인트에 추가
try:
    deploy_svc = AppDeployService(settings)
    deploy_svc._delete_app_resources(app.pod_name)
except Exception as e:
    logger.error(f"K8s undeploy failed: {e}")
```

---

## Task 5: auth-check — slug 기반 URL 파싱

**Files:**
- Modify: `auth-gateway/app/routers/apps.py:135-269` — auth_check 함수

현재 URL 파싱: `/apps/{owner_username}/{app_name}/`
변경: `/apps/{slug}/{app_name}/` → slug로 owner 조회

```python
# 기존: owner_username = parts[1]
# 변경: slug = parts[1] → User.app_slug == slug → owner_username
slug = parts[1] if len(parts) >= 2 else ""
owner = db.query(User).filter(User.app_slug == slug).first()
if not owner:
    raise HTTPException(404, "앱을 찾을 수 없습니다")
owner_username = owner.username
```

---

## Task 6: 앱 코드 복사 API

**Files:**
- Modify: `container-image/fileserver.py` — `POST /api/apps/prepare-deploy` 엔드포인트 추가

배포 전 앱 코드를 `deployed/` 경로로 복사:
```python
def _handle_apps_prepare_deploy(self):
    """POST /api/apps/prepare-deploy — 앱 코드를 deployed 경로로 복사."""
    body = self._read_body()
    data = json.loads(body)
    app_name = data.get('name', '')
    app_path = data.get('path', '')
    
    # 소스 검증
    if not os.path.isdir(app_path):
        self._send_json(404, {"error": f"앱 디렉토리를 찾을 수 없습니다: {app_path}"})
        return
    
    # 대상 경로: ~/workspace/deployed/{app_name}/current/
    deploy_base = os.path.join(self.directory, 'deployed', app_name)
    deploy_current = os.path.join(deploy_base, 'current')
    
    try:
        # 기존 deployed 디렉토리 정리
        if os.path.exists(deploy_current):
            shutil.rmtree(deploy_current)
        os.makedirs(deploy_current, exist_ok=True)
        
        # 앱 코드 복사 (node_modules, __pycache__, .git 제외)
        for item in os.listdir(app_path):
            if item in ('node_modules', '__pycache__', '.git', '.venv', 'venv'):
                continue
            src = os.path.join(app_path, item)
            dst = os.path.join(deploy_current, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        
        self._send_json(200, {"prepared": True, "deploy_path": deploy_current})
    except Exception as e:
        self._send_json(500, {"error": f"코드 복사 실패: {str(e)}"})
```

---

## Task 7: Hub UI — executeDeploy 흐름 수정

**Files:**
- Modify: `container-image/fileserver.py` — JS executeDeploy 함수

배포 흐름: prepare-deploy → deploy API 순차 호출:
```javascript
function executeDeploy() {
  var t = document.getElementById('hubToast');
  t.textContent = '배포 준비 중: ' + deployAppNameVal;
  t.style.display = 'block';
  
  // 1단계: 앱 코드 복사
  localFetch('/api/apps/prepare-deploy', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: deployAppNameVal, path: deployAppPathVal})
  }).then(function(data) {
    if (!data.prepared) {
      t.textContent = '배포 준비 실패: ' + (data.error || '');
      t.style.background = '#da3633';
      setTimeout(function() { t.style.display = 'none'; t.style.background = ''; }, 4000);
      return;
    }
    // 2단계: 플랫폼 배포 API 호출
    t.textContent = '배포 중: ' + deployAppNameVal;
    var visibility = document.querySelector('input[name="deployVisibility"]:checked').value;
    return apiFetch('/apps/deploy', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        app_name: deployAppNameVal,
        visibility: visibility,
        app_port: 3000,
        acl_usernames: visibility === 'company' ? [] : deploySelectedUsernames
      })
    });
  }).then(function(data) {
    if (!data) return; // prepare 실패 시
    closeDeployModal();
    if (data.app_url) {
      t.textContent = '배포 완료! URL: https://claude.skons.net' + data.app_url;
    } else {
      t.textContent = '배포 실패: ' + (data.detail || '알 수 없는 오류');
      t.style.background = '#da3633';
    }
    setTimeout(function() { t.style.display = 'none'; t.style.background = ''; }, 5000);
    loadMyApps();
  }).catch(function(err) {
    t.textContent = '배포 오류: ' + (err.message || err);
    t.style.background = '#da3633';
    setTimeout(function() { t.style.display = 'none'; t.style.background = ''; }, 5000);
  });
}
```

---

## Task 8: 빌드 & 배포 & 검증

**Step 1:** container-image 빌드 & ECR 푸시
**Step 2:** auth-gateway 빌드 & ECR 푸시  
**Step 3:** auth-gateway rollout restart
**Step 4:** 기존 사용자 app_slug 일괄 생성 (DB migration)

**검증:**
1. Hub에서 "공유" 클릭 → 모달 열림 → "배포하기" 클릭
2. 코드 복사 확인: `deployed/{app_name}/current/` 존재
3. K8s 리소스 확인: `claude-apps` 네임스페이스에 Pod/Service/Ingress 생성
4. URL 접속: `/apps/{slug}/{app_name}/` → 앱 표시
5. 다른 사용자 접속 → webapp-login → ACL 검증
6. undeploy → K8s 리소스 삭제 확인

---

## 팀 구성

| 팀 | 담당 |
|---|------|
| **DB팀** | Task 1: app_slug 마이그레이션 + 기존 사용자 일괄 생성 |
| **Backend팀** | Task 2-5: deploy service slug 적용, 버그 수정, auth-check, undeploy |
| **Frontend팀** | Task 6-7: prepare-deploy API, executeDeploy 흐름 수정 |
| **QA+Review** | 전체 검증 루프 |
