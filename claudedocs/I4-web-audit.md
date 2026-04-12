# I4: Hub UI / admin-dashboard Word/PPTX Flow 감사

**Date**: 2026-04-12  
**Scope**: `container-image/fileserver.py` (Hub UI JS), `auth-gateway/app/routers/viewers.py`, `admin-dashboard/`  
**Focus**: xlsx vs docx/pptx 분기 차이, URL encoding, documentType 매핑, iframe/postMessage 프로토콜

---

## 1. Admin-Dashboard (Next.js 15)

**결론: 파일 뷰어 코드 없음 — 범위 외**

- `admin-dashboard/app/` 전체 grep 결과 `docx|pptx|xlsx|onlyoffice` 매칭 0건
- Admin dashboard는 세션/사용량 모니터링 전용 (Next.js pages: `infra`, `security`, `usage`, `users`, `audit` 등)
- OnlyOffice 통합 없음. 파일 뷰어 플로우와 완전 무관.

---

## 2. Hub UI 파일 오픈 경로 (container-image/fileserver.py)

Hub UI HTML은 `fileserver.py`의 Python f-string 템플릿에 인라인 포함됨 (정적 HTML 파일 아님).

### 2a. OFFICE_EXTENSIONS 정의

**fileserver.py:3187**
```js
var OFFICE_EXTENSIONS = {'xlsx':1,'xls':1,'csv':1,'docx':1,'doc':1,'pptx':1,'ppt':1};
```

**viewers.py:123**
```python
OFFICE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".docx", ".doc", ".pptx", ".ppt", ".odt", ".ods", ".odp", ".rtf"}
```

| 확장자 | Hub UI 인식 | viewers.py 처리 가능 |
|--------|------------|---------------------|
| .xlsx / .xls / .csv | ✅ | ✅ |
| .docx / .doc | ✅ | ✅ |
| .pptx / .ppt | ✅ | ✅ |
| .odt / .ods / .odp | ❌ | ✅ |
| .rtf | ❌ | ✅ |

**⚠️ Gap**: Hub UI가 `.odt`, `.ods`, `.odp`, `.rtf`를 OFFICE_EXTENSIONS로 인식하지 않음.  
- 더블클릭/미리보기 클릭 시 `openPreview()`에서 else 분기 → 미리보기 없이 아무것도 열리지 않음
- viewers.py 백엔드는 이 확장자들을 처리 가능하나 Hub UI에서 진입불가
- **xlsx vs docx/pptx 차이 아님** — 정의된 7개 타입 모두 동일하게 처리됨

### 2b. openPreview() — 더블클릭 핸들러

**fileserver.py:3060-3070**
```js
function openPreview(d) {
  var ext = getFileExt(d.name);          // d.name에서 확장자 추출
  var username = '{user_id}';            // 서버 렌더 시 치환
  if (MARKDOWN_EXTENSIONS[ext]) {
    window.open('/api/v1/viewers/markdown/' + encodeURIComponent(username) + '/' + encodeURIComponent(d.path), '_blank');
  } else if (OFFICE_EXTENSIONS[ext]) {
    window.open('/api/v1/viewers/onlyoffice/' + encodeURIComponent(username) + '/' + encodeURIComponent(d.path), '_blank');
  } else if (PREVIEW_EXTENSIONS[ext]) {
    window.open('/api/v1/viewers/file/' + encodeURIComponent(username) + '/' + encodeURIComponent(d.path), '_blank');
  }
  // else: 지원 안 하면 아무 동작 없음
}
```

**xlsx vs docx vs pptx 분기 없음**: 모두 동일한 `/api/v1/viewers/onlyoffice/{user}/{path}` URL 사용.

**URL encoding**: `encodeURIComponent(d.path)` 적용  
- `d.path`는 browse API의 `os.path.join(rel_path, name)` (Linux 슬래시)
- `folder/report.docx` → `folder%2Freport.docx`
- uvicorn이 `%2F` → `/` 디코딩 → FastAPI `{file_path:path}` 정상 캡처
- xlsx, docx, pptx 모두 동일 인코딩 적용 — 차이 없음

### 2c. ctxEdit() — 우클릭 편집

**fileserver.py:3206-3213**
```js
function ctxEdit() {
  if (!OFFICE_EXTENSIONS[ext]) { hideContextMenu(); return; }
  var url = '/api/v1/viewers/onlyoffice/edit/' + encodeURIComponent(username) + '/' + encodeURIComponent(feContextTarget.path);
  window.open(url, '_blank');
}
```

xlsx, docx, pptx 동일 URL 패턴. 분기 없음.

### 2d. ctxCoEdit() — 공유 파일 공동편집

**fileserver.py:3216-3222**
```js
function ctxCoEdit() {
  var info = getSharedMountInfo(feContextTarget.path);  // team/ 접두사 파악
  var url = '/api/v1/viewers/onlyoffice/shared/' + encodeURIComponent(info.mount_id) + '/' + encodeURIComponent(info.file_path);
  window.open(url, '_blank');
}
```

공유 데이터셋(`team/` prefix)일 때만 활성화. 파일 타입 분기 없음.

### 2e. getFileType() 표시 맵 — 경미한 UI 버그

**fileserver.py:2941-2945**
```js
var t = {
  'py':'Python', 'js':'JavaScript', ..., 
  'xlsx':'Excel',   // ← xlsx만 있음
  'sql':'SQL', 'pdf':'PDF', ...
};
// docx, pptx, doc, ppt 없음
```

| 파일 | 타입 컬럼 표시 |
|------|---------------|
| .xlsx | "Excel" |
| .docx | (없음 — 확장자 그대로 표시) |
| .pptx | (없음 — 확장자 그대로 표시) |

파일 목록 UI의 **타입 컬럼 표시**만 영향. 뷰어 동작과 무관.

---

## 3. viewers.py 서버사이드 documentType 매핑

**viewers.py:419-425**
```python
def _onlyoffice_doc_type(ext: str) -> str:
    if ext in {".xlsx", ".xls", ".csv", ".ods"}:
        return "cell"
    if ext in {".pptx", ".ppt", ".odp"}:
        return "slide"
    return "word"   # docx, doc, odt, rtf, 기타 모두 "word"
```

- OnlyOffice `documentType` 필드 → `_build_onlyoffice_config()`에서 `config["documentType"]`로 삽입
- **서버사이드에서 정확히 분기됨**. 클라이언트 JS에서 documentType을 제어하는 코드 없음.

---

## 4. /api/v1/viewers/onlyoffice/config/{filename} 엔드포인트 분석

**viewers.py:564-591**

```python
@router.get("/onlyoffice/config/{filename:path}")
async def onlyoffice_config(filename: str, ...):
    """OnlyOffice 뷰어 설정 JSON 반환.
    Hub UI가 이 설정을 받아 OnlyOffice api.js로 iframe을 생성한다.
    """
```

**⚠️ 주석과 실제 구현의 불일치**:
- 주석: "Hub UI가 이 설정을 받아 OnlyOffice api.js로 iframe을 생성한다"
- 실제: Hub UI(`fileserver.py`)는 이 config 엔드포인트를 **전혀 호출하지 않음**
- Hub UI는 `window.open()`으로 완전한 HTML 뷰어 페이지 직접 오픈
- 이 엔드포인트는 외부 API 클라이언트용으로 보이거나, 과거 구현의 잔재

---

## 5. iframe / postMessage 프로토콜

**결론: 존재하지 않음**

- `fileserver.py` 전체 grep: `postMessage`, `addEventListener('message')`, `<iframe>` — 매칭 없음
- `viewers.py` 전체 grep: `postMessage`, `iframe` — 매칭 없음 (docstring 언급 1건 제외)
- Hub UI는 `window.open('...', '_blank')`로 새 탭/윈도우 오픈 방식 사용
- documentType 기반 postMessage 분기 없음 (Excel/Word/PPTX 모두 동일)

---

## 6. 한글 파일명 NFC/NFD 처리

| 위치 | NFC/NFD 처리 |
|------|-------------|
| `fileserver.py` `_handle_download` (line 281) | ✅ NFC/NFD fallback 있음 |
| `viewers.py` `/file/{username}/{file_path}` (line 361) | ✅ NFC/NFD fallback 있음 |
| `viewers.py` `_build_onlyoffice_config` file_download_url | ❌ normalization 없이 f-string 삽입 |
| `fileserver.py` `openPreview()` | ❌ `d.path`를 normalize 없이 encodeURIComponent |

**흐름 평가**:
1. Hub UI: `d.path`(OS 네이티브) → `encodeURIComponent` → URL
2. auth-gateway: `{file_path:path}` 파라미터 수신 → `_personal_download_url(username, file_path)` 호출
3. `_personal_download_url` → f-string에 raw `file_path` 삽입 → OnlyOffice DS에 제공
4. OnlyOffice DS가 이 URL로 auth-gateway의 `/file/{username}/{file_path}` 호출
5. `/file/` 엔드포인트에 NFC/NFD fallback 있음 → 파일 접근 성공

**xlsx vs docx/pptx 차이 없음** — 모두 동일한 NFC/NFD 경로 통과.

---

## 7. 파일 다운로드 URL 미인코딩 이슈

**viewers.py:460-467** (`_build_onlyoffice_config`)
```python
file_download_url = (
    f"http://auth-gateway.platform.svc.cluster.local"
    f"/api/v1/viewers/file/{token_owner}/{filename}?token={file_token}"
)
```

- `filename`이 URL 인코딩 없이 f-string에 삽입됨
- 예: `filename = "보고서 최종.docx"` → URL에 공백/한글 포함
- OnlyOffice DS의 HTTP 클라이언트 동작에 의존
- **모든 office 타입 동일하게 영향** (xlsx 한글도 동일)
- Token 기반 인증이므로 다른 경로로의 우회 위험은 없으나, OnlyOffice DS가 URL을 잘못 처리하면 404 또는 파일 로드 실패 가능

---

## 8. 요약: xlsx vs docx/pptx 분기 차이 여부

| 항목 | xlsx | docx | pptx | 차이 있음? |
|------|------|------|------|-----------|
| Hub UI OFFICE_EXTENSIONS 인식 | ✅ | ✅ | ✅ | ❌ 동일 |
| openPreview() URL 패턴 | `/onlyoffice/{u}/{p}` | 동일 | 동일 | ❌ 동일 |
| ctxEdit() URL 패턴 | `/onlyoffice/edit/{u}/{p}` | 동일 | 동일 | ❌ 동일 |
| encodeURIComponent 적용 | ✅ | ✅ | ✅ | ❌ 동일 |
| 서버 documentType | "cell" | "word" | "slide" | ✅ 정확히 분기됨 |
| 편집가능 여부 (EDITABLE_EXTENSIONS) | ✅ | ✅ | ✅ | ❌ 동일 |
| NFC/NFD 처리 경로 | 동일 | 동일 | 동일 | ❌ 동일 |
| getFileType 표시 | "Excel" | (없음) | (없음) | ✅ UI 표시만 다름 |
| 자동 SQLite 변환 | ✅ (10MB+) | ❌ | ❌ | ✅ 업로드 시만 다름 |

**클라이언트 JS에서 xlsx vs docx/pptx 분기는 없음. 모든 OFFICE 타입이 동일 URL 패턴으로 라우팅됨.**

---

## 9. 잠재적 4xx 예상 지점

| 시나리오 | 상태 | 원인 |
|---------|------|------|
| `.odt/.rtf` 더블클릭 | 아무 일 없음 | Hub UI OFFICE_EXTENSIONS에 없어 openPreview 실패 |
| 한글 파일명 OnlyOffice DS 다운로드 | 가능성 있음 | download_url 미인코딩 (viewers.py:465) |
| 공유가 아닌 파일에 "함께 편집" 클릭 | 실행 안 됨 | `getSharedMountInfo` null → 조기 return |
| config 엔드포인트 직접 호출 시 | 200 OK | 동작함. 단 Hub UI에서 호출 안 됨 |

---

## 10. 결론

- **Admin-dashboard**: 파일 뷰어 플로우와 완전 무관
- **Hub UI JS**: xlsx/docx/pptx 분기 없음 — 동일 URL 패턴 사용
- **viewers.py**: `_onlyoffice_doc_type()` 서버사이드에서 정확히 분기
- **config 엔드포인트 주석 오류**: "Hub UI가 iframe 생성" → 실제는 window.open 방식
- **OFFICE_EXTENSIONS gap**: Hub UI에서 `.odt/.ods/.odp/.rtf` 미인식
- **download_url 미인코딩**: 모든 타입 공통 이슈 (xlsx/docx/pptx 모두 동일)
- **postMessage/iframe 프로토콜**: 없음

> Word/PPTX 특이 버그가 있다면 클라이언트 분기가 아닌 **OnlyOffice DS 자체의 Word/PPTX 처리 문제** (폰트, 컨버터, documentType "word"/"slide" 응답)일 가능성이 높음 → I2(인프라) 및 I1(서버 경로) 감사 결과와 교차 검토 필요.
