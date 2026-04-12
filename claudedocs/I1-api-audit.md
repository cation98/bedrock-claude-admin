# I1 Audit Report: viewers.py / k8s_service.py 파일타입별 경로 Divergence

**작성**: 2026-04-12  
**감사자**: api teammate (Task #1)  
**세션**: c38eda2b-dcda-480c-a493-ff5fb5352829  
**범위**: auth-gateway/app/routers/viewers.py (1292 LOC), auth-gateway/app/services/k8s_service.py (990 LOC)  
**목적**: Excel(xlsx) 정상 동작 vs Word(docx)/PPTX(pptx) 실패의 코드 경로 divergence 감사  

---

## Executive Summary

코드 분석 결과, **Excel과 Word/PPTX를 구분하는 명시적 분기 로직은 존재하지 않는다.** 대신 다음 3개의 잠재적 원인이 발견됐다:

1. **One-time 파일 토큰** — OO DS가 Word/PPTX 처리 시 변환 파이프라인으로 파일 URL 재요청 시 두 번째 요청 401 실패 (**가장 유력한 원인**, 위험도 H)
2. **`filetype` 파라미터 미사용** — `_save_edited_file`이 OO DS로부터 받은 `filetype`을 무시, 레거시 포맷 변환 시 파일 내용-경로 불일치 (위험도 H)
3. **테스트 커버리지 전무** — 모든 callback/save 테스트가 `.xlsx`만 사용, docx/pptx save 흐름은 한 번도 테스트되지 않음 (위험도 H)

---

## 발견 목록

### F1 [위험도: H] One-time 파일 토큰 — OO DS 재요청 시 401

**파일**: `viewers.py:87-107`, `viewers.py:461-467`, `viewers.py:607-611`

**설명**:  
`_consume_file_token` 함수는 첫 사용 시 토큰을 즉시 소비한다:
- Redis: `r.getdel(f"ftoken:{token}")` — 원자적 get+delete
- Memory fallback: `_file_tokens.pop(token, None)` — 즉시 제거

`_personal_download_url`(L605-611)과 `_build_onlyoffice_config` default URL 생성(L461-467)에서 생성된 one-time 토큰이 OO Document Server에 전달된다.

**Excel vs Word/PPTX 차이**:  
OO DS가 Excel(`.xlsx`) 파일을 열 때는 네이티브 OOXML 처리로 단일 HTTP 요청(토큰 1회 소비)으로 충분하다. 반면 Word(`.docx`) / PPTX(`.pptx`)는 내부 변환 파이프라인(font resolution, style sheet parsing, image extraction 등)에서 추가 HTTP 요청이 발생할 수 있다. 두 번째 요청은 이미 소비된 토큰으로 인해 `401 Invalid or expired token`을 반환 → OO DS "문서 로드 실패" 오류.

**근거**:
```python
# viewers.py:94 (Redis)
val = r.getdel(f"ftoken:{token}")  # 원자적 get+delete

# viewers.py:104 (Memory fallback)
data = _file_tokens.pop(token, None)  # pop = 즉시 삭제

# viewers.py:329-331 (stream_file 토큰 검증)
if not token_data:
    raise HTTPException(status_code=401, detail="Invalid or expired token")
```

**Excel 대비 차이**: Excel → 단일 요청 → 성공. Word/PPTX → OO DS 변환 파이프라인 → 재요청 → 401.

---

### F2 [위험도: H] `_save_edited_file` — `filetype` 파라미터 완전 미사용

**파일**: `viewers.py:988`, `viewers.py:1118-1180`

**설명**:  
콜백 핸들러(L988)에서 `body.get("filetype")`을 `_save_edited_file`에 전달하지만, 함수 내부에서 이 파라미터를 **단 한 번도 사용하지 않는다**. 컨테이너 경로는 항상 `session.file_path`에서만 계산된다:

```python
# viewers.py:1153-1157
if session.is_shared:
    container_path = f"/home/node/workspace/shared-data/{session.file_path}"
else:
    raw = session.file_path
    container_path = raw if raw.startswith("/") else f"/home/node/workspace/{raw}"
```

**Excel vs Word/PPTX 차이**:  
- Excel(`.xlsx`): OO DS가 xlsx로 저장 → `filetype="xlsx"` → `session.file_path`도 `.xlsx` → 불일치 없음
- Word(`.doc` 레거시): OO DS가 docx로 변환 저장 → `filetype="docx"` but `session.file_path` = `*.doc` → **파일 내용은 docx, 경로는 `.doc` → 포맷 손상**
- Word(`.docx`): 정상 케이스에서는 불일치 없으나, `filetype` 검증 로직 자체가 없어 예외 상황 무방비

**근거**:
```python
# viewers.py:1118 - filetype 받지만
async def _save_edited_file(session: EditSession, download_url: str, filetype: str | None) -> None:

# viewers.py:1153-1157 - filetype은 사용되지 않고 session.file_path만 사용
raw = session.file_path
container_path = raw if raw.startswith("/") else f"/home/node/workspace/{raw}"
```

`filetype`이 `None`인 경우(OO DS가 미전송 시)도 무처리 — 어떤 검증도 수행되지 않는다.

---

### F3 [위험도: H] 테스트 커버리지: 모든 Save/Callback 테스트가 `.xlsx`만 사용

**파일**: `tests/test_viewers.py:647-838`

**설명**:  
`TestCallbackSaveFlow` 및 관련 integration 테스트 전체가 `.xlsx` 파일만 사용한다:

| 테스트 | 사용 파일 |
|--------|----------|
| `test_callback_status_2_downloads_and_saves` | `docs/report.xlsx`, `r.xlsx` |
| `test_callback_status_2_deletes_session` | `report.xlsx` |
| `test_callback_status_2_kubectl_cp_failure` | `big.xlsx` |
| `_save_edited_file` integration test | `Editor.xlsx` |
| `test_callback_status_6_force_save` | `force.xlsx` |
| 기타 status=2 테스트 | `deal.xlsx`, `rot.xlsx` 등 |

**`.docx` / `.pptx` save 흐름 테스트: 0개**

이로 인해 Word/PPTX save 흐름의 어떤 회귀도 CI에서 탐지되지 않는다.

---

### F4 [위험도: M] MIME_MAP — 모든 오피스 확장자 누락

**파일**: `viewers.py:110-121`, `viewers.py:387`

**설명**:  
`MIME_MAP`에 오피스 파일 확장자가 없어 `stream_file`에서 모든 오피스 파일에 `application/octet-stream` 반환:

```python
# viewers.py:110-121 - .xlsx, .docx, .pptx 모두 없음
MIME_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    # ... 이미지/텍스트만 있음
    # .xlsx, .docx, .pptx, .doc, .ppt, .odt, .ods, .odp 전부 누락
}

# viewers.py:387
media_type = MIME_MAP.get(ext, "application/octet-stream")
```

**Excel vs Word/PPTX 차이**: 없음. Excel도 동일하게 `octet-stream` → **discriminating factor 아님.**  
OO DS는 HTTP Content-Type보다 config의 `document.fileType` 필드를 우선 사용하므로 직접적 원인은 아니다.

**단**, OO DS 일부 버전이 Content-Type을 보조 지표로 사용하거나, 특정 파일 타입(Word/PPTX)에서 더 엄격하게 검증하면 문제가 될 수 있다.

**올바른 MIME 매핑 기준**:
- `.docx`, `.doc`, `.odt`: `application/vnd.openxmlformats-officedocument.wordprocessingml.document` / `application/msword` / `application/vnd.oasis.opendocument.text`
- `.pptx`, `.ppt`, `.odp`: `application/vnd.openxmlformats-officedocument.presentationml.presentation` / `application/vnd.ms-powerpoint` / `application/vnd.oasis.opendocument.presentation`
- `.xlsx`, `.xls`, `.ods`: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` / `application/vnd.ms-excel` / `application/vnd.oasis.opendocument.spreadsheet`

---

### F5 [위험도: M] `_personal_download_url` — 파일 경로 URL 미인코딩

**파일**: `viewers.py:605-611`

**설명**:  
```python
def _personal_download_url(username: str, file_path: str) -> str:
    file_token = _create_file_token(username, file_path)
    return (
        f"http://auth-gateway.platform.svc.cluster.local"
        f"/api/v1/viewers/file/{token_owner}/{file_path}?token={file_token}"
    )
```

`file_path`가 f-string에 직접 삽입되어 **percent-encoding 없음**. 한글/공백 포함 파일명(예: `보고서 2026.docx`) 처리 시 URL 파싱 오류 발생 가능.

**Excel vs Word/PPTX 차이**: 없음 — 파일명 인코딩 문제는 확장자와 무관.  
단, 한글 파일명을 가진 Word/PPTX 파일이 더 많을 경우 실질적으로 더 자주 발생.

---

### F6 [위험도: L] `permissions.download` = `editable` — view-only 시 전 타입 다운로드 불가

**파일**: `viewers.py:474`

**설명**:  
```python
permissions = {
    "download": editable,  # L474
    ...
}
```

`onlyoffice_viewer`(view-only)에서 `editable=False` → `download: false`. OO UI 다운로드 버튼 비활성.

**Excel vs Word/PPTX 차이**: 없음. View-only Excel도 동일하게 `download: false`.

---

### F7 [위험도: L] `settings.onlyoffice_url` 정의되었으나 viewers.py에서 미사용

**파일**: `config.py:83`, `viewers.py` 전체

**설명**:  
`config.py:83`: `onlyoffice_url: str = "http://onlyoffice.claude-sessions.svc.cluster.local"`

`viewers.py`에서 `settings.onlyoffice_url`은 사용되지 않는다. 대신 hardcoded cluster DNS:
- `callbackUrl`: `"http://auth-gateway.platform.svc.cluster.local/api/v1/viewers/onlyoffice/callback"` (L504-507)
- 파일 download URL: `"http://auth-gateway.platform.svc.cluster.local"` (L465, 609)
- P2-BUG2 rewrite: `onlyoffice.claude-sessions.svc.cluster.local` hardcoded (L1142)

`settings.onlyoffice_url`은 k8s_service.py, 헬스체크, 어드민 도구 등에서만 사용 가능성이 있으며, viewers.py 동작에는 영향 없음.

---

## k8s_service.py 분석

`write_local_file_to_pod` (L829-867), `_validate_container_path` (L798-827), `_copy_local_to_pod_sync` (L917-979):

- **파일 타입별 분기 없음**: 모든 확장자에 동일한 tar pipe + K8s exec API 사용
- `_validate_container_path`: `/home/node/workspace` 기준 경로 검증만 수행, 확장자 체크 없음
- `_copy_local_to_pod_sync`: `arcname=dest_path.lstrip("/")`, tar `xmf - -C /` 로 단일 파일 복사 — 파일 내용 무관

**k8s_service.py에서 Excel/Word/PPTX 간 divergence 없음.**

---

## 발견 요약표

| # | 발견 | 파일:라인 | 위험도 | Excel 영향 | Word/PPTX 영향 | Excel/Word 차이 |
|---|------|----------|-------|-----------|----------------|----------------|
| F1 | One-time 파일 토큰 — OO DS 재요청 401 | viewers.py:94, 463, 607 | **H** | 단일 요청 → OK | 변환 시 재요청 → 401 | **有** |
| F2 | `filetype` 파라미터 미사용 | viewers.py:1118, 988 | **H** | 포맷 동일 → OK | 레거시 변환 시 손상 | 有 (레거시 포맷) |
| F3 | save/callback 테스트 `.xlsx`만 사용 | test_viewers.py:647-838 | **H** | 테스트됨 | 미테스트 | **有** |
| F4 | MIME_MAP 오피스 확장자 누락 | viewers.py:110-121, 387 | M | octet-stream | octet-stream | 無 |
| F5 | `_personal_download_url` URL 미인코딩 | viewers.py:605-611 | M | 동일 | 동일 | 無 |
| F6 | `permissions.download` = editable | viewers.py:474 | L | 동일 | 동일 | 無 |
| F7 | `settings.onlyoffice_url` 미사용 | config.py:83 | L | N/A | N/A | 無 |

---

## 핵심 결론

**Excel vs Word/PPTX 코드 레벨 discriminating factor:**

1. **F1 (One-time token)**: 가장 유력한 원인. OO DS가 Word/PPTX 처리 시 변환 파이프라인에서 파일 URL 재요청 시 token 소진 → 401. Excel은 네이티브 처리로 단일 요청.

2. **F2 (filetype 미사용)**: 레거시 포맷(`.doc`, `.ppt`) 편집 후 저장 시 파일 내용-경로 불일치. 신규 포맷(`.docx`, `.pptx`)에서는 즉각 문제 없으나 검증 로직 부재.

3. **F3 (테스트 gap)**: Excel 저장 흐름만 검증됨. Word/PPTX 저장 흐름의 회귀 탐지 불가.

**주의**: viewers.py/k8s_service.py 코드에서 발견된 divergence는 가설적 원인이다. 확진을 위해서는 OO DS 9.3.1 실제 파일 요청 패턴(단일 vs 다중 요청) 검증이 필요하다.

---

## 참조

- `OFFICE_EXTENSIONS` (viewers.py:123): `{".xlsx", ".xls", ".csv", ".docx", ".doc", ".pptx", ".ppt", ".odt", ".ods", ".odp", ".rtf"}`
- `EDITABLE_EXTENSIONS` (viewers.py:127): `{".xlsx", ".docx", ".pptx", ".odt", ".ods", ".odp"}`
- `_onlyoffice_doc_type` (viewers.py:419-425): 매핑 정확 — xlx/ods→cell, pptx/ppt/odp→slide, else→word
- `_CONTAINER_BASE_DIR` (k8s_service.py:796): `/home/node/workspace`
- OO JWT secret 필수(min_length=32, no placeholder): config.py:86-94
