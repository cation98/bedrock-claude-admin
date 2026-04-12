# I5: JWT / Allowlist / URL 검증 보안 감사

**역할**: 감시자 (flag only — 코드 수정 없음)  
**대상**: OnlyOffice 파일 로드/저장 경로의 JWT 서명, SSRF allowlist, URL 검증이 Word/PPTX에서만 실패하는지 감사  
**파일**: `auth-gateway/app/routers/viewers.py`, `auth-gateway/app/core/security.py`, `auth-gateway/app/core/config.py`, commit `55938cd`  
**감사일**: 2026-04-12

---

## 요약

| # | Severity | CVSS 추정 | 제목 | Word/PPTX 특수성 |
|---|----------|-----------|------|-----------------|
| F1 | **High** | 7.3 | Config JWT에 `exp` 클레임 없음 — 무기한 재사용 가능 | 간접 (긴 편집 세션) |
| F2 | **High** | 6.5 | 파일 토큰 1회용 소비 + 대용량 파일 재시도 실패 | **직접 (Word/PPTX 특수)** |
| F3 | **High** | 6.1 | httpx가 리다이렉트를 무검증 추적 — SSRF via redirect | Word/PPTX 변환 경로에서 고위험 |
| F4 | Medium | 5.0 | localhost 재작성이 임의 포트 보존 | OO DS 침해 시 |
| F5 | Medium | 4.3 | SSRF allowlist에 단축 hostname 포함 | allowlist 우회 위험 |
| F6 | Medium | 4.3 | CSP frame-ancestors가 JSON endpoint에만 설정, HTML 편집 페이지 미설정 | 전체 유형 |
| F7 | Low | 2.5 | `if settings.onlyoffice_jwt_secret:` 죽은 분기 | 코드 스멜 |
| F8 | Low | 2.0 | DOCX/PPTX 내 외부 HTTP 리소스 → 브라우저 Mixed Content 차단 가능성 | **Word/PPTX 특수** |

---

## 상세 발견 사항

---

### F1: Config JWT에 `exp` 클레임 없음 — 무기한 재사용 가능
**Severity**: High | **CVSS 추정**: 7.3 (AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:L/A:N)

**위치**: `viewers.py:520-522`

```python
if settings.onlyoffice_jwt_secret:
    token = jose_jwt.encode(config, settings.onlyoffice_jwt_secret, algorithm="HS256")
    config["token"] = token
```

**문제**:
- `jose_jwt.encode(config, ...)` 에 `exp` 클레임을 **전혀 추가하지 않음**.
- 생성된 JWT는 만료 시각이 없어 이론상 영구 유효.
- 콜백 JWT(`viewers.py:895-900`)에는 `require_exp: True`를 강제하는 것과 **비대칭**:
  ```python
  # 콜백 수신 시 (OO DS → auth-gateway): exp 강제
  options={"require_exp": True, "verify_exp": True}
  # config 발행 시 (auth-gateway → OO DS): exp 없음  ← 미대칭
  ```

**공격 시나리오**:
1. 공격자가 브라우저 DevTools 또는 네트워크 스니핑으로 HTML 소스의 `config.token` 캡처
2. 해당 JWT를 OO DS에 재제출하여 문서 세션 재사용
3. 특히 PPTX 편집처럼 세션 지속 시간이 긴 경우 노출 창이 확대

**OO 표준 확인**:
OO 공식 문서("Security")에서 `"exp"` 를 config JWT에 포함하도록 **권장**하지만 필수는 아님. 그러나 replay window 최소화 관점에서 `exp` 포함은 best practice.

**Word/PPTX 특수성**: 직접적이지 않으나 PPTX 편집 세션은 xlsx보다 오래 열려 있어 토큰 노출 창이 넓어짐.

---

### F2: 파일 토큰 1회용 소비 + 대용량 파일 재시도 실패  
**Severity**: High | **CVSS 추정**: 6.5 (AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:H)  
**Word/PPTX 직접 원인 후보**

**위치**: `viewers.py:88-107` (소비 로직), `viewers.py:462-466` (토큰 URL 생성), `viewers.py:62` (TTL=300s)

```python
def _consume_file_token(token: str) -> dict | None:
    ...
    if r:
        val = r.getdel(f"ftoken:{token}")  # 원자적 get+delete — 1회용
```

```python
file_token = _create_file_token(token_owner, filename)
file_download_url = (
    f"http://auth-gateway.platform.svc.cluster.local"
    f"/api/v1/viewers/file/{token_owner}/{filename}?token={file_token}"
)
```

**문제**:
- 파일 토큰은 **Redis `getdel`로 원자적으로 1회 소비** (메모리 fallback도 동일: `_file_tokens.pop(token, None)`).
- OO DS가 파일을 다운로드하다 연결이 끊기면(partial read, timeout) 자동 재시도 시 **401 (토큰 소비됨)** → OO DS가 파일 로드 실패.
- TTL 300초 이내에도 재시도 불가.

**Word/PPTX 특수성 — 직접적**:
| 파일 유형 | 평균 크기 범위 | 재시도 발생 가능성 |
|-----------|--------------|-----------------|
| XLSX (스프레드시트) | 10KB ~ 1MB | 낮음 |
| DOCX (Word) | 100KB ~ 30MB (embedded 이미지) | 보통 |
| PPTX (PowerPoint) | 1MB ~ 100MB+ (고해상도 이미지, 동영상) | **높음** |

stream 파일 endpoint(`viewers.py:352`)의 `timeout=120.0s`보다 PPTX 전송 시간이 길어지면 httpx가 중단 → OO DS 재시도 → 토큰 없음.

**파생 위협**: 토큰이 "consumed but download incomplete" 상태면 해당 파일은 해당 세션에서 다시 로드할 수 없음 (사용자 관점 "파일이 열리지 않음").

---

### F3: httpx가 리다이렉트 무검증 추적 — SSRF via redirect
**Severity**: High | **CVSS 추정**: 6.1 (AV:N/AC:H/PR:H/UI:N/S:C/C:H/I:N/A:N)  
(OO DS compromise 전제)

**위치**: `viewers.py:1163-1164`

```python
async with httpx.AsyncClient(timeout=120.0) as http:  # follow_redirects 기본값 = True (httpx 0.23+)
    async with http.stream("GET", download_url) as resp:
```

**문제**:
- `httpx.AsyncClient`는 **기본적으로 리다이렉트를 추적** (`follow_redirects=True` in httpx ≥ 0.23).
- `_validate_callback_download_url()` 은 **최초 URL만 검증** — redirect target은 검증하지 않음.
- 공격 시나리오 (OO DS compromised):
  1. OO DS가 `url = "http://onlyoffice/cache/files/..."` (allowlist 통과)를 콜백에 포함
  2. auth-gateway가 이 URL 다운로드 시작
  3. OO DS가 HTTP 302 → `http://169.254.169.254/latest/meta-data/` 반환
  4. httpx가 redirect 추적 → **AWS IMDS 접근 성공** → 자격증명 탈취 가능

**Word/PPTX 경로 특수성**:
OO DS의 Word/PPTX 내부 변환 파이프라인은 파일 저장 후 내부 스토리지 경로 변경이 xlsx보다 복잡. 변환 후 redirect가 발생할 수 있는 OO DS 내부 구조와 맞물림.

**증거**: `viewers.py:1208` Markdown viewer의 동일 패턴:
```python
async with httpx.AsyncClient(timeout=30.0) as http:  # 여기도 미설정
```

---

### F4: localhost → cluster DNS 재작성이 임의 포트 보존
**Severity**: Medium | **CVSS 추정**: 5.0 (AV:N/AC:H/PR:H/UI:N/S:U/C:L/I:L/A:N)

**위치**: `viewers.py:1136-1148`

```python
_port_suffix = (
    f":{_parsed.port}" if _parsed.port and _parsed.port != 80 else ""
)
_rewritten = _parsed._replace(
    netloc=f"onlyoffice.claude-sessions.svc.cluster.local{_port_suffix}"
).geturl()
```

**문제**:
- `http://localhost:6379/...` → rewrite → `http://onlyoffice.claude-sessions.svc.cluster.local:6379/...`
- SSRF allowlist는 `localhost` 를 통과시키고, 재작성은 hostname만 교체, **포트는 보존**.
- OO DS pod에 포트 6379(Redis), 5432(PostgreSQL) 등이 노출된 경우 비정상 포트 접근 가능.
- 실제 공격은 OO DS 침해를 전제하므로 exploitability는 낮음.

**수정 방향 (flag only)**: 재작성 시 포트를 `80` (또는 OO DS 기본 포트)로 고정.

---

### F5: SSRF allowlist 단축 hostname 포함으로 namespace 외 서비스 도달 가능
**Severity**: Medium | **CVSS 추정**: 4.3

**위치**: `viewers.py:1090, 1095` (55938cd 이후)

```python
_ALLOWED_CALLBACK_URL_HOSTS = {
    "onlyoffice.claude-sessions.svc.cluster.local",  # FQDN ✓
    "onlyoffice.claude-sessions.svc",                # ✓
    "onlyoffice.claude-sessions",                    # ✓
    "onlyoffice",                                    # ← 위험: 단축 hostname
    "documentserver",                                # ← 위험: 단축 hostname
    ...
}
```

**문제**:
- K8s Pod의 DNS search domain은 `claude-sessions.svc.cluster.local` 등.
- 그러나 search domain 적용은 Pod DNS resolver에서 일어남; `urlparse`는 raw hostname만 봄.
- `"onlyoffice"` 가 허용되면 OO DS compromise 시 동일 이름의 **다른 namespace 서비스**(예: `onlyoffice.attacker-ns.svc.cluster.local`)로 향하는 단축 host를 콜백에 포함 가능.
- `"documentserver"` 역호환 항목도 마찬가지.
- 실제 공격은 K8s 내부 접근 + OO DS 침해 전제이므로 exploitability 제한적.

---

### F6: CSP frame-ancestors가 JSON endpoint에만, HTML 편집 페이지는 무방비
**Severity**: Medium | **CVSS 추정**: 4.3 (AV:N/AC:L/PR:N/UI:R/S:C/C:N/I:L/A:N) — Clickjacking

**위치**: `viewers.py:586-591` vs `viewers.py:619, 745`

```python
# /onlyoffice/config/{filename} — JSON 응답에 설정 (의미 없음)
return JSONResponse(
    content=config,
    headers={"Content-Security-Policy": "frame-ancestors 'self'"},  # JSON에 CSP는 irrelevant
)

# /onlyoffice/edit/{username}/{file_path:path} — HTML 응답에 CSP 없음
return HTMLResponse(content=_render_onlyoffice_html(file_path, config))  # No CSP

# /onlyoffice/{username}/{file_path:path} — HTML 응답에 CSP 없음  
return HTMLResponse(content=_render_onlyoffice_html(file_path, config))  # No CSP
```

**문제**:
- `frame-ancestors 'self'` 헤더는 **HTML 응답에 설정되어야** 해당 페이지가 타 origin iframe에 포함되는 것을 방지.
- JSON API 응답에 CSP를 설정해도 브라우저는 이를 해당 JSON 리소스의 임베드 정책으로만 처리 → **실질적 보호 없음**.
- 실제 편집 HTML 페이지는 어떤 origin에서든 `<iframe src="...">` 로 로드 가능 → **Clickjacking** 공격 가능.
- Word/PPTX 특수성: 없음 (전 유형 공통). 단, 기밀 Word 문서에 대한 clickjacking이 XLSX보다 정보 가치 높음.

**OO 표준**: OO DS는 내부적으로 iFrame을 사용하므로 `X-Frame-Options: SAMEORIGIN` 또는 `frame-ancestors 'self'`를 HTML viewer에 설정해야 함.

---

### F7: `if settings.onlyoffice_jwt_secret:` 죽은 분기
**Severity**: Low | **CVSS 추정**: 2.5

**위치**: `viewers.py:520`, `config.py:86-95`

```python
# config.py: 필수 필드 + 최소 32자 + placeholder 거부
onlyoffice_jwt_secret: str = Field(..., min_length=32)

@field_validator("onlyoffice_jwt_secret")
@classmethod
def _reject_placeholder_jwt_secret(cls, v: str) -> str:
    if v.startswith("CHANGE_ME_"):
        raise ValueError(...)
    return v

# viewers.py: JWT 서명이 조건부인 것처럼 코드 작성
if settings.onlyoffice_jwt_secret:  # 항상 True — 죽은 분기
    token = jose_jwt.encode(config, ...)
    config["token"] = token
```

**문제**:
- `onlyoffice_jwt_secret`은 `Field(...)`(필수)이고 min_length=32이므로 앱 시작 자체가 실패하지 않는 한 `settings.onlyoffice_jwt_secret`은 항상 truthy.
- `if` 조건부 코드는 "JWT 서명이 선택사항"이라는 **잘못된 신호**를 미래 개발자에게 줌.
- 실수로 `else` 분기를 추가하거나 조건을 변경하면 JWT 없는 config가 전송될 위험.

---

### F8: DOCX/PPTX 내 외부 HTTP 리소스 → Mixed Content 차단
**Severity**: Low | **CVSS 추정**: 2.0 (가용성 영향 — 렌더링 실패)  
**Word/PPTX 특수 (XLSX 해당 없음)**

**위치**: `viewers.py:527-561` (`_render_onlyoffice_html`)

**문제**:
- PPTX/DOCX는 외부 이미지(`http://...`), OLE 개체, 폰트 참조를 파일 내부에 포함 가능.
- 플랫폼이 HTTPS로 서비스될 경우, OO DS가 렌더링 결과 HTML에 `http://...` 리소스를 포함하면 브라우저가 **Mixed Content 차단**.
- XLSX(스프레드시트)는 외부 미디어 임베딩이 드물어 해당 없음.
- 콘솔에는 에러가 표시되나 사용자에게는 이미지/개체가 공백으로 보임.

**주의**: OO DS가 내부 렌더링에서 외부 URL을 direct로 HTML에 삽입하는지는 OO DS 버전(9.3.1)의 동작에 의존. Mixed Content가 실제 발생하는지는 브라우저 Network 탭 확인 필요.

---

## Word/PPTX 특수 케이스 요약

| 점검 항목 (Task 정의) | 결과 |
|--------------------|------|
| 1. `_validate_callback_download_url` — Word/PPTX 저장 시 경로/쿼리 차이? | **F3 참조**: URL 호스트 검증은 공통이나 redirect 미검증이 Word/PPTX 변환 경로에서 위험 |
| 2. JWT payload key 순서가 documentType별로 달라 서명 검증 실패? | **FALSE POSITIVE**: Python 3.7+ dict는 삽입순 고정이고, `_build_onlyoffice_config`의 key 순서는 documentType 무관하게 동일. OO DS도 object-level 비교 수행 |
| 3. SSRF allowlist에 Word/PPTX 전용 내부 URL 누락? | **F5 참조**: allowlist 자체가 너무 관대(단축 hostname). 별도 Word/PPTX URL은 없음 |
| 4. Mixed Content — Word/PPTX만 외부 리소스 참조? | **F8 참조**: Word/PPTX 특수 위험 확인 |
| 5. CSP frame-ancestors가 Word/PPTX에만 거부 정책? | **F6 참조**: 반대 — HTML editor 전체에 CSP 없음(JSON endpoint에만 오적용됨) |

---

## 핵심 발견: F2가 Word/PPTX 로드 실패의 **직접 원인 후보**

F2 (파일 토큰 1회용 소비)는 기능적 가용성 버그이자 보안 설계 결함:
- PPTX 다운로드 실패 → 1회용 토큰 소비됨 → OO DS 재시도 → 401 → 렌더링 불가
- XLSX보다 PPTX/DOCX에서 발생 빈도가 명확히 높음 (파일 크기 10-100x)
- 기존 버그픽스(P2-BUG1~3)가 이 경로를 다루지 않음

---

## 테스트 커버리지 gap (참고)

`tests/test_viewers.py` 기준:
- `_validate_callback_download_url` SSRF 테스트: **없음**
- httpx redirect 검증 테스트: **없음**
- config JWT `exp` 클레임 테스트: **없음** (콜백 JWT exp는 L607에서 테스트됨)
- 파일 토큰 retry 시나리오 테스트: **없음**
- HTML editor 페이지 CSP 헤더 테스트: **없음**

상세 테스트 gap은 I6 담당 팀원 참조.

---

*감사자: security teammate | 세션: c38eda2b*
