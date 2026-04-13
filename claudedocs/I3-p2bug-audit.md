# I3: P2-BUG1/BUG2/BUG3 패치 재감사 — Word/PPTX Gap 리포트

**감사일**: 2026-04-12  
**담당**: review teammate  
**대상 커밋**: 56cc8c2, c6d2d1e, f3dcd0e, ca9a756  
**방법**: `git show <sha>` diff 정독 + `Read` 로 현재 코드 상태 확인 + grep 분기 추적

---

## 1. 커밋별 diff 요약 + filetype 분기 존재 여부

| 커밋 | 제목 | 변경 파일 | filetype/ext 분기 | 판정 |
|------|------|-----------|-------------------|------|
| 56cc8c2 | P2-BUG1: envelope 포맷 복원 | `viewers.py` | **없음** — `body = decoded.get("payload", decoded)` 한 줄, 전 파일 유형 동일 경로 | ✅ 코드 일반화 |
| c6d2d1e | P2-BUG1: view-only 런타임 버그 | `viewers.py` | **없음** — salt 주입(`_doc_key_personal`/`_doc_key_shared`) + status=2/4 → DELETE 모두 ext 무관 | ✅ 코드 일반화 |
| f3dcd0e | P2-BUG2: localhost → cluster DNS rewrite | `viewers.py` | **없음** — `urlparse(download_url).hostname` 체크만, ext 무관 | ✅ 코드 일반화 |
| ca9a756 | P2-BUG3: kubectl → k8s stream + tar | `k8s_service.py` | **없음** — `tar.add(local_path, arcname=dest_path.lstrip("/"))` ext 무관, 모든 바이너리 동일 처리 | ✅ 코드 일반화 |

**코드 수준 결론**: 4개 패치 모두 filetype/ext 로 분기하지 않는다. `.docx`/`.pptx` 도 동일 경로를 탄다.

---

## 2. 테스트 커버리지 gap (주요 발견)

### 2-A. BUG1 — Envelope 포맷 (56cc8c2)

| 테스트 | 사용 파일 | filetype 클레임 | docx/pptx 커버 |
|--------|-----------|-----------------|----------------|
| `test_callback_accepts_envelope_format` | `envelope.xlsx` | `"filetype": "xlsx"` | ❌ 없음 |

**Gap**: OnlyOffice 9.x 가 `.docx`/`.pptx` 편집 후 저장 콜백을 envelope JWT 로 전송할 때 `"filetype": "docx"` / `"pptx"` 클레임이 올바르게 파싱되는지 테스트 없음. 코드는 동일 경로를 타지만 OO 가 Word/PPTX 에서도 envelope 를 쓰는지는 테스트로 보장되지 않음.

### 2-B. BUG1 — view-only 런타임 (c6d2d1e)

| 테스트 | 사용 파일 | docx/pptx 커버 |
|--------|-----------|----------------|
| `test_callback_status_2_deletes_session` | `report.xlsx` | ❌ |
| `test_reopen_after_save_is_editable` | `deal.xlsx` | ❌ |
| `test_key_rotation_on_save` | (xlsx) | ❌ |
| `test_callback_status_4_cleanup` | (xlsx) | ❌ |

**Gap**: 저장 완료(status=2) → 행 DELETE → 재오픈 시 editable 유지 전체 시나리오가 `.docx`/`.pptx` 기준으로 한 번도 검증되지 않음. config 조회 테스트(line 304, 313)는 docx/pptx 포함되어 있지만 **콜백 + 재오픈 flow** 는 xlsx 전용.

### 2-C. BUG2 — localhost rewrite (f3dcd0e)

| 테스트 | 사용 파일 | filetype | docx/pptx 커버 |
|--------|-----------|----------|----------------|
| `test_save_rewrites_localhost_to_cluster_dns` | `rewrite.xlsx` | `"xlsx"` | ❌ 없음 |

**Gap**: rewrite 로직 자체는 hostname 만 본다. 그러나 OO 가 Word/PPTX 저장 후 내려보내는 URL 도 실제로 `localhost` 를 쓰는지 확인한 테스트 없음. `_save_edited_file` 의 `filetype: str | None` 파라미터는 **함수 바디 어디서도 사용되지 않으므로** docx/pptx 전달 시에도 동일 동작 보장은 코드 상으로는 OK — 단 테스트 미비.

### 2-D. BUG3 — k8s stream + tar (ca9a756)

| 테스트 | 사용 파일 | docx/pptx 커버 |
|--------|-----------|----------------|
| `test_uses_kubernetes_stream_not_kubectl_subprocess` | `file.xlsx`, `src.xlsx` | ❌ 없음 |

**Gap**: `_copy_local_to_pod_sync` 은 파일 확장자와 무관한 binary-safe tar pipe. 그러나 `.docx`/`.pptx` 를 tar 로 묶어 Pod 에 복사하는 경로를 직접 검증한 테스트 없음.

---

## 3. 코드 추가 확인 사항

### `_save_edited_file` 의 `filetype` 파라미터 미사용

```python
# viewers.py:1118
async def _save_edited_file(session: EditSession, download_url: str, filetype: str | None) -> None:
```

`filetype` 은 서명에 있지만 함수 바디(1118–1180)에서 **한 번도 참조되지 않는다**.  
→ 현재 버그는 없지만, dead parameter 가 향후 혼선을 유발할 수 있음.

### `EDITABLE_EXTENSIONS` 정의 (c6d2d1e 이후 현재 코드)

```python
# viewers.py:127
EDITABLE_EXTENSIONS = {".xlsx", ".docx", ".pptx", ".odt", ".ods", ".odp"}
```

진입점(edit/shared 엔드포인트)에서 docx/pptx 는 이미 포함되어 있음. 문제는 여기가 아니라 콜백 이후 flow 의 테스트 공백.

---

## 4. Commit 메시지 scope 제한 문구 확인

| 커밋 | 메시지 본문 Excel 제한 문구 |
|------|-----------------------------|
| 56cc8c2 | 없음. "OnlyOffice 9.x outbox callback" 로 범위 기술 (파일 타입 무관) |
| c6d2d1e | 없음. "재편집 view-only" 로 기술, Excel 한정 아님 |
| f3dcd0e | 없음. "OO DS 8.2.2 localhost 인식 문제" — 파일 타입 무관 |
| ca9a756 | 없음. "auth-gateway 이미지에 kubectl 없음" — 파일 타입 무관 |

커밋 메시지에 "Excel 기준 검증" 또는 scope 제한 문구는 없음.

---

## 5. Gap 요약 테이블

| ID | Bug | Gap 유형 | 위험도 | 설명 |
|----|-----|----------|--------|------|
| G1 | BUG1 (envelope) | 테스트 | 중 | `test_callback_accepts_envelope_format` 가 xlsx 전용. Word/PPTX envelope 콜백 미검증 |
| G2 | BUG1 (view-only) | 테스트 | **고** | 저장→재오픈 editable 유지 flow 가 docx/pptx 기준 전혀 없음. 가장 핵심 시나리오 |
| G3 | BUG2 (localhost) | 테스트 | 중 | rewrite 테스트가 xlsx 전용. docx/pptx download URL rewrite 미검증 |
| G4 | BUG3 (tar copy) | 테스트 | 중 | `write_local_file_to_pod` 가 docx/pptx 를 정상 복사하는지 미검증 |
| G5 | BUG2/공통 | 코드품질 | 저 | `_save_edited_file` 의 `filetype` 파라미터가 바디에서 미사용 — dead parameter |

---

## 6. 결론

**코드 수준에서 Word/PPTX 에만 적용 안 되는 분기는 없다.**  
4개 패치 모두 ext/filetype 로 분기하지 않으며, `EDITABLE_EXTENSIONS` 에 docx/pptx 가 포함된다.

**그러나 테스트는 사실상 xlsx 전용으로 작성됐다.**  
P2-BUG1/BUG2/BUG3 핵심 시나리오(envelope 파싱, 저장→재오픈 editable, localhost rewrite, tar copy) 의 신규 테스트 8건 중 docx/pptx fixture 를 쓰는 케이스가 0건이다.

**가장 높은 위험(G2)**: 편집→저장→재오픈 view-only 재현 가능성은 BUG1 이 해결한 핵심 문제인데, Word/PPTX 파일로 이 flow 를 한 번도 돌리지 않았다.  
→ OO 가 Word/PPTX 저장 시 다른 envelope 구조나 status 코드를 쓴다면 버그가 남아 있을 수 있다.

**권고**: I3 에서 식별된 G1~G4 gap 에 대해 docx/pptx fixture 테스트를 추가하는 별도 태스크(I6 재현 스크립트와 연계 가능)가 필요하다.
