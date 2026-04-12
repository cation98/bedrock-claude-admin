# I7: Dockerfile / container-image / DevOps 설정 감사

**감사 범위**: `container-image/`, `auth-gateway/Dockerfile`, `infra/k8s/platform/onlyoffice.yaml`, `infra/local-dev/07-onlyoffice.yaml`, `auth-gateway/app/routers/viewers.py` (MIME/download 관련)  
**조사 날짜**: 2026-04-12  
**담당**: devops  
**상태**: 완료 — NO FIXES

---

## 요약

| 가설 | 결론 |
|------|------|
| H1: fileserver MIME 오탐 (python-magic 없음) | **FALSE** — `mimetypes.guess_type()` 으로 .docx/.pptx 정확히 반환 |
| H2: NFC/NFD 처리 Word/PPTX 차이 | **FALSE** — 동일 로직 적용, 대칭 처리 |
| H3: ttyd/Claude Code 인코딩 의존성 누락 | **FALSE** — 관련 없음 |
| H4: Dockerfile locale 설정 | **FALSE** — `LANG=C.UTF-8` 정상, 파일 형식 처리에 무관 |
| H5: healthcheck에 Word converter 누락 | **CONFIRMED (indirect)** — `/healthcheck`로는 converter 기동 여부 확인 불가 |
| **추가 발견**: platform OO manifest 누락 env vars | **HIGH RISK** — 모든 형식에 영향 (Word-vs-PPTX 차이 설명은 못함) |
| **추가 발견**: `MIME_MAP`에 Office 형식 없음 | **CONFIRMED gap** — 모든 Office 파일 → `application/octet-stream` (대칭적) |

---

## 발견 사항 상세

### 1. [CONFIRMED GAP] `viewers.py` MIME_MAP에 Office 형식 미등록

**파일**: `auth-gateway/app/routers/viewers.py:110-121`

```python
MIME_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    # ... 이미지/텍스트만 등록됨
    ".csv": "text/csv; charset=utf-8",
    # .docx, .pptx, .xlsx, .doc, .ppt 등 없음
}
```

**코드**: `stream_file()` 라인 387:
```python
media_type = MIME_MAP.get(ext, "application/octet-stream")
```

- `.docx`, `.pptx`, `.xlsx` 등 모든 Office 확장자 → fallback `application/octet-stream`
- OO DS가 auth-gateway에서 파일을 받을 때 Content-Type이 `application/octet-stream`
- OO DS는 config JSON의 `fileType` 필드("docx", "pptx" 등)를 우선하므로 즉각적 버그는 아님
- **Word와 PPTX가 동일하게 fallback되므로 두 형식 간 차이를 설명하지 못함**

반면, **Pod 내 `fileserver.py`** 는 정확한 MIME을 반환:
```python
# fileserver.py:294
content_type, _ = mimetypes.guess_type(real_target)
# .docx → application/vnd.openxmlformats-officedocument.wordprocessingml.document  ✅
# .pptx → application/vnd.openxmlformats-officedocument.presentationml.presentation  ✅
```

테스트 검증 (Python 3.x 내장 DB):
```
test.docx: application/vnd.openxmlformats-officedocument.wordprocessingml.document ✅
test.pptx: application/vnd.openxmlformats-officedocument.presentationml.presentation ✅
test.xlsx: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet ✅
```

**결론**: fileserver는 정확한 MIME을 Pod → auth-gateway로 보내지만, auth-gateway의 `stream_file`이 이를 무시하고 `MIME_MAP` 자체 로직으로 override. Office 파일은 전부 `application/octet-stream`으로 OO DS에 전달됨. 비록 OO DS 동작에 직접 영향이 없더라도, 정상화 대상.

---

### 2. [HIGH RISK] platform OO manifest에 필수 env vars 누락

**파일 비교**: `infra/k8s/platform/onlyoffice.yaml` vs `infra/local-dev/07-onlyoffice.yaml`

| 설정 | local-dev | platform | 영향 |
|------|-----------|----------|------|
| `ALLOW_PRIVATE_IP_ADDRESS=true` | ✅ 있음 | ❌ **없음** | OO DS가 K8s 내부 IP(Pod IP)로의 파일 다운로드 차단 |
| `JWT_INBOX_ENABLED=false` | ✅ 있음 | ❌ **없음** | OO DS가 파일 다운로드 요청에 JWT 검증 강제 적용 |
| `ONLYOFFICE_DOCS_PARAMS` | ✅ 있음 | ❌ **없음** | local.json 의 inbox JWT 비활성화 override 안 됨 |
| `postStart` lifecycle hook | ✅ 있음 | ❌ **없음** | local.json 패치 로직 미실행 |

**중요**: platform manifest에서 이 env vars 들이 누락되어 있다면 프로덕션의 OO DS는:
1. Pod IP(예: `10.x.x.x`) 로의 파일 다운로드 요청을 사설 IP 차단으로 거부할 수 있음
2. `auth-gateway`가 file token을 포함해 OO DS에 응답해도 OO DS 자체 JWT inbox 검증으로 재거부 가능

**단, 이는 모든 형식(Word, PPTX, Excel)에 동일하게 적용되므로 Word-vs-PPTX 차이를 직접 설명하지 않음.** 만약 이 설정이 프로덕션에 반영되지 않았다면 전체 OO 기능 중단이 예상됨. 실제로 일부 형식이 동작한다면, `kubectl patch` 등 외부 수단으로 이미 적용되었거나, 다른 경로의 문제임.

---

### 3. [CONFIRMED] healthcheck가 converter pipeline 기동 여부를 검증하지 않음

**파일**: `infra/k8s/platform/onlyoffice.yaml:67-78` 및 `infra/local-dev/07-onlyoffice.yaml:61-70`

```yaml
livenessProbe:
  httpGet:
    path: /healthcheck
    port: 80
  initialDelaySeconds: 60
readinessProbe:
  httpGet:
    path: /healthcheck
    port: 80
  initialDelaySeconds: 30
```

**OO DS `/healthcheck` 응답 구조**:
```json
{"status": "OK"}
```

이 엔드포인트는 코어 supervisord 서비스(`nginx`, `redis`)가 실행 중이면 200을 반환하지만, 실제 변환 작업에 필요한 서비스들(`ds:docservice`, `ds:converter`, `ds:spellchecker`)의 개별 건강 상태를 검증하지 않음.

**실제 영향**:
- OO DS Pod가 readiness를 통과하더라도 내부 x2t 변환 바이너리가 준비되지 않은 상태일 수 있음
- Word 변환 경로(`docx`→처리)와 Presentation 변환 경로(`pptx`→처리)는 내부적으로 별도의 dll/so 모듈을 사용
- converter 워밍업 시간 차이로 포드 재시작 직후 특정 형식만 실패할 수 있음

**더 상세한 검증이 필요한 엔드포인트**: OO DS 8.2.2에는 `/healthcheck` 외에 `/info/info.json`, `/aggregator/convinfo` 등의 내부 상태 엔드포인트가 존재하나 현 manifest에서 미사용.

---

### 4. [FALSE] fileserver의 python-magic / libmagic 미설치 → 문제 없음

**파일**: `container-image/Dockerfile:14-37`

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget unzip git vim-tiny less jq ca-certificates \
    python3 python3-pip python3-venv \
    postgresql-client \
    build-essential cmake libjson-c-dev libwebsockets-dev \
    tini
```

`python-magic`, `libmagic1`, `file` 패키지 — **미설치**. `mimetypes.guess_type()`은 OS mime DB 없이 Python 내장 DB만 사용하며 .docx/.pptx를 정확히 반환. 문제 없음.

---

### 5. [FALSE] Dockerfile locale 설정

**파일**: `container-image/Dockerfile:199`

```dockerfile
ENV CLAUDE_CODE_USE_BEDROCK=1 \
    AWS_REGION=us-east-1 \
    TERM=xterm-256color \
    LANG=C.UTF-8
```

`C.UTF-8`은 UTF-8 인코딩을 지원하는 표준 POSIX locale. `ko_KR.UTF-8`과 비교했을 때 Office 파일 처리(특히 내부 XML 파싱)에 차이 없음. 한글 파일명 처리는 `unicodedata.normalize()`를 통해 프로그래밍적으로 처리됨(fileserver.py:282-287, viewers.py:361-365).

---

### 6. [FALSE] 한글 파일명 NFC/NFD Word/PPTX 차이

**파일**: `container-image/fileserver.py:282-287`, `auth-gateway/app/routers/viewers.py:361-365`

두 레이어 모두 동일 로직 적용:
1. 원래 경로로 시도
2. 404이면 NFC↔NFD 변환 후 재시도

`.docx`와 `.pptx` 모두 동일 경로를 통과 → Word-vs-PPTX 차이 없음.

---

### 7. [관찰] `viewers/onlyoffice-config/config.json`의 `download_disabled`

**파일**: `container-image/viewers/onlyoffice-config/config.json`

```json
{
  "onlyoffice": {
    "download_disabled": true,
    "print_disabled": false
  }
}
```

이 파일은 auth-gateway의 `viewers.py`에서 직접 로드되지 않음. `_build_onlyoffice_config()`에서 permissions는 `editable` 파라미터로 직접 제어됨:
```python
"download": editable,   # 편집 모드에서만 다운로드 허용
```

config.json은 참조용 문서 또는 다른 컴포넌트에서 사용하는 설정 템플릿으로 보임. OO DS 동작에 직접 영향 없음.

---

### 8. [정상] auth-gateway Dockerfile — 의존성 최소

**파일**: `auth-gateway/Dockerfile`

```dockerfile
FROM python:3.12-slim
RUN apt-get install -y libpq-dev gcc  # psycopg2 빌드용만
```

- `python-magic`, `libmagic` 없음 — 의도적
- MIME 감지를 auth-gateway에서 수행하지 않음 (MIME_MAP 방식)
- Office 파일 스트리밍 시 Content-Type은 MIME_MAP → fallback 처리 (위의 #1 참조)

---

## 종합 판단: Word-vs-PPTX 차이 설명 가능한 DevOps 요인

| 요인 | Word-PPTX 차이 설명 | 비고 |
|------|---------------------|------|
| fileserver MIME_MAP 없음 | ❌ 동일 fallback | 정상화 필요 |
| Platform OO missing env | ❌ 모든 형식 동일 영향 | 확인/수정 필요 |
| healthcheck converter 미검증 | ⚠️ 간접 가능성 | converter 기동 순서 차이 |
| locale, NFC/NFD | ❌ 차이 없음 | |
| Dockerfile 의존성 | ❌ 무관 | |

**결론**: DevOps 레이어 단독으로는 Word-vs-PPTX 차이를 완전히 설명하지 못함. 주요 가설들(H1-H4)은 기각됨. Platform OO manifest의 env var 누락은 별도 확인이 필요한 독립적인 고위험 gap이며, healthcheck의 converter 미검증(H5)은 형식별 intermittent failure의 간접 요인이 될 수 있음.

**핵심 지점을 I1(viewers.py divergence 분석), I2(OO 이미지 감사)로 전달 필요**.

---

## 참고: 파일 다운로드 전체 경로

```
OO DS (pod)
  → GET http://auth-gateway.platform.svc.cluster.local
         /api/v1/viewers/file/{username}/{file_path}?token={one_time_token}
       [auth-gateway stream_file()]
         → Content-Type: MIME_MAP.get(ext, "application/octet-stream")
            ↳ .docx/.pptx → "application/octet-stream"   ← GAP
         → proxies from:
  → GET http://{pod_ip}:8080/api/download?path={encoded_path}
       [fileserver.py _handle_download()]
         → Content-Type: mimetypes.guess_type()  ← 정확한 MIME (무시됨)
         → Content-Length: os.path.getsize()     ← 정상
         → Content-Disposition: RFC5987 인코딩   ← 정상
```
