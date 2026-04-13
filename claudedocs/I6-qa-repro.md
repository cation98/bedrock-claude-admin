# I6 QA Audit — Word/PPTX 커버리지 Gap + 재현 스텝

**작성일**: 2026-04-12  
**감사 범위**: `auth-gateway/tests/` (test_viewers.py + test_k8s_service.py)  
**대상 함수**: `_onlyoffice_doc_type`, `_build_onlyoffice_config`, `_save_edited_file`

---

## 1. 테스트 fixture 확장자 분포

`test_viewers.py` 기준 (총 85줄에 확장자 등장):

| 확장자 | 등장 횟수 | 주요 사용 컨텍스트 |
|--------|----------|-------------------|
| **xlsx** | **61** | Config API, Callback 저장 흐름, Edit 모드, Shared ACL, 키 회전, Force-save 등 전 영역 |
| docx   | 4  | Config API (permissions.download=False), documentType 매핑, Edit 모드 HTML(mode=edit) |
| pptx   | 4  | Config API (CSP 헤더), documentType 매핑, Edit 모드 HTML(forcesave=True) |
| csv    | 2  | documentType 매핑 (cell) 만 |
| ods    | 1  | documentType 매핑 only |
| xls    | 1  | documentType 매핑 only |
| ppt    | 1  | documentType 매핑 only |
| doc    | 1  | documentType 매핑 only |
| odt    | 1  | documentType 매핑 only |
| odp    | 1  | documentType 매핑 only |
| rtf    | 1  | documentType 매핑 only |

**결론**: xlsx가 전체 fixture의 72%를 차지. docx/pptx는 Config/HTML 계층에만 국한됨.

---

## 2. 함수별 커버리지 분석

### 2-1. `_onlyoffice_doc_type(ext)` — `viewers.py:419`

```python
def _onlyoffice_doc_type(ext: str) -> str:
    if ext in {".xlsx", ".xls", ".csv", ".ods"}: return "cell"
    if ext in {".pptx", ".ppt", ".odp"}:         return "slide"
    return "word"  # docx, doc, odt, rtf → 기본값
```

| 테스트 | 상태 |
|--------|------|
| `test_document_type_mapping` — cell(xlsx,xls,csv,ods), slide(pptx,ppt,odp), word(docx,doc,odt,rtf) | ✅ FULL |

**커버리지**: 완전. 단위 테스트 추가 불필요.

---

### 2-2. `_build_onlyoffice_config(filename, ...)` — `viewers.py:428`

| 테스트 시나리오 | xlsx | docx | pptx |
|----------------|------|------|------|
| HTTP 200 반환 | ✅ | ✅ (download_disabled) | ✅ (CSP header) |
| `document.fileType` 값 | ✅ | ✅ (fileType 추출) | ✅ (fileType 추출) |
| `documentType` 매핑 | ✅ | ✅ | ✅ |
| `permissions.*` 전체 검증 | ✅ | ⚠️ download만 | ❌ |
| JWT `token` 필드 구조 | ✅ | ❌ | ❌ |
| `editorConfig.mode=edit` | ✅ | ✅ | ❌ |
| `editorConfig.customization.forcesave` | ✅ | ❌ | ✅ |
| File download URL 포함 | ✅ | ❌ | ❌ |
| `EDITABLE_EXTENSIONS` 강제 view-only 분기 | ✅ | ❌ | ❌ |

> `EDITABLE_EXTENSIONS = {".xlsx", ".docx", ".pptx", ".odt", ".ods", ".odp"}` (viewers.py:127)  
> .docx/.pptx 는 편집 가능 확장자이므로 `editable=True`로 호출 시 permissions.edit=True가 정상 동작해야 하지만 테스트 없음.

**커버리지**: docx/pptx 모두 PARTIAL — 특히 JWT 토큰 + permissions 완전 검증 미비.

---

### 2-3. `_save_edited_file(session, download_url, filetype)` — `viewers.py:1118` ← **가장 치명적 GAP**

| 테스트 | xlsx | docx | pptx |
|--------|------|------|------|
| status=2 → `_save_edited_file` 호출됨 | ✅ | ❌ | ❌ |
| 저장 성공 → EditSession 행 DELETE | ✅ | ❌ | ❌ |
| 저장 실패(RuntimeError) → status='save_failed' | ✅ | ❌ | ❌ |
| localhost URL → cluster DNS rewrite | ✅ | ❌ | ❌ |
| Envelope JWT 포맷 콜백 | ✅ | ❌ | ❌ |
| Force-save(status=6) 흐름 | ✅ | ❌ | ❌ |
| container_path 계산 (personal) | ✅ | ❌ | ❌ |
| container_path 계산 (shared) | ✅ | ❌ | ❌ |

**추가 발견 — `filetype` 파라미터는 dead parameter**:  
`_save_edited_file`이 `filetype: str | None`을 받지만 함수 본문 어디서도 사용하지 않음 (viewers.py:1118~1180). 콜백의 `filetype` 필드가 검증·활용되지 않으며 `container_path`는 오직 `session.file_path`에서만 결정됨. 이는 `filetype`이 callback body에서 오는 값이므로 OnlyOffice DS가 확장자를 바꿔 전달해도 auth-gateway는 무조건 원래 session path로 덮어씀 — Word/PPTX에서 이 동작 미검증.

---

## 3. `test_k8s_service.py` uncommitted 변경 내용

```diff
+            if kwargs.get("_preload_content", True):
+                return ""
+            return _FakeStreamResp()
```

**의미**: `write_local_file_to_pod`가 내부적으로 두 종류의 `stream()` 호출을 함:
- `_preload_content=True` (짧은 명령: mkdir, stat 등) → 문자열 반환
- `_preload_content=False` (tar 스트림) → WebSocket-like 객체 반환

기존 mock은 항상 `_FakeStreamResp()`를 반환했기 때문에 짧은 명령이 `""` 대신 객체를 받아 테스트가 오작동할 수 있었음. 이 수정은 P2-BUG3 (`kubectl → python k8s client stream + tar`)의 후속 테스트 안정화.

---

## 4. 재현 스텝 (로컬 pytest)

### 4-1. 환경 구성

```bash
cd auth-gateway
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio httpx
```

### 4-2. 기존 viewers 테스트 실행 (커버리지 확인)

```bash
pytest tests/test_viewers.py -v 2>&1 | tee /tmp/viewers_test_output.txt
# 예상: 44 passed (Excel 편중 커버리지)
```

### 4-3. 신규 Word/PPTX skeleton 테스트 실행

```bash
pytest tests/test_viewers_word_pptx.py -v
# 예상: 대부분 SKIP (skeleton) — 실제 환경 없이도 구조 검증 가능
```

### 4-4. k8s_service 수정 테스트

```bash
pytest tests/test_k8s_service.py -v -k "TestWriteLocalFileToPod"
# uncommitted 변경 포함 — _preload_content 분기 동작 검증
```

---

## 5. 가장 치명적인 Gap

**`_save_edited_file`가 docx/pptx 세션에서 한 번도 테스트되지 않음.**

재현 시나리오:
1. 사용자가 `document.docx`를 /edit 엔드포인트로 열기 → EditSession 생성 (`file_path="document.docx"`)
2. OnlyOffice DS가 편집 완료 후 `status=2, url="http://localhost/cache/files/Editor.docx"` 콜백 전송
3. `_save_edited_file`이 호출되어 localhost URL rewrite → cluster DNS, httpx 다운로드 → `write_local_file_to_pod` 호출
4. **현재 테스트 없음** → P2-BUG2/BUG3 패치가 docx/pptx 경로에서도 동작하는지 미검증

pytest 제안:
```python
# test_viewers_word_pptx.py 참조
class TestSaveFlowWordPptx:
    def test_callback_status2_docx_downloads_and_saves(...)
    def test_callback_status2_pptx_downloads_and_saves(...)
    def test_localhost_url_rewrite_docx(...)
    def test_localhost_url_rewrite_pptx(...)
```

---

## 6. 산출물 파일

| 파일 | 설명 |
|------|------|
| `claudedocs/I6-qa-repro.md` | 본 문서 |
| `claudedocs/I6-repro-word.sh` | curl 재현 스크립트 (dry-run) |
| `auth-gateway/tests/test_viewers_word_pptx.py` | Skeleton 테스트 (pytest.skip 포함) |
