# I8 — OnlyOffice Word/PPTX 다운로드 실패 조사 종합 (Phase 1 완료)

**Date**: 2026-04-12
**Session**: onlyoffice-word-pptx-download-fix (c38eda2b)
**Team**: oo-word-pptx-debug (7 agents parallel)

## 사용자 원문 재해석

> "onlyoffice word, pptx에도 excel때와 동일하게 다운로드 실패등이 관찰된다"

→ "Excel이 과거 P2-BUG 시리즈로 고쳐지기 전에 겪던 **동일한 증상**이 현재 Word/PPTX에서도 관찰된다."

7개 팀 모두 **Excel과 Word/PPTX 사이의 코드 경로 divergence는 없다**고 수렴 → 근본원인은 "패치가 `.xlsx` fixture로만 검증돼 Word/PPTX는 동일 버그에 여전히 노출" 가설에 무게.

## 가설 Top 3 (우선순위 순)

### 🔴 H1: One-time 파일 토큰 + OO DS 다중 fetch (api F1 + security F2)

**증거 교차**:
- `viewers.py:88-107` `_consume_file_token` — Redis `getdel` / 메모리 `pop` 원자 소비
- OnlyOffice Document Server는 Word/PPTX 변환 파이프라인에서 원본 파일을 **>1회** fetch할 가능성:
  - 첫 번째: conversion daemon이 포맷 분석용
  - 두 번째: editor가 실시간 렌더링용
  - 재시도: 연결 끊김 시 exponential backoff
- Excel은 내장 SpreadsheetEditor 단일 fetch로 처리 → 1회 소비 OK
- **파일 크기 상관성**: PPTX(1-100MB) > DOCX(100KB-10MB) > XLSX(대체로 작음) — 타임아웃→재시도 확률 크기순 일치

**재현 예상**:
- auth-gateway 로그: `.docx` 파일 열기 시 `token consumed` 다음 `ftoken:<hash> missing` 또는 401
- OO DS 로그: `ERROR downloadFile: HTTP 401` or `download failed`

**검증 방법 (수정 전 minimal test)**:
1. `/api/v1/viewers/file/{username}/{file_path}` 엔드포인트에 `logger.info(f"file_token_request path={file_path} token_prefix={token[:8]}")` 추가 1줄
2. `.docx` 열기 시도 → 로그에 동일 `token_prefix` 2+회 관찰되면 H1 확정
3. `.xlsx` 동일 파일 → 1회만 관찰되면 대비 확정

**확정 시 수정안 (Phase 3→4)**:
- 옵션 A (권장): 토큰을 TTL 기반 N회 사용으로 변경. `getdel` → `get` + 별도 TTL expire. OnlyOffice 변환 완료까지 (5분) 유효, TTL 만료 시 auto GC
- 옵션 B: document_key에 짧은 HMAC을 URL path 자체에 실어 idempotent fetch 허용 (토큰 자체 제거)
- 옵션 C: 다운로드 전용 JWT(짧은 exp)로 대체

### 🔴 H2: 프로덕션 K8s 매니페스트 env var 4개 누락 (k8s F1/F2 + devops 교차)

**증거**:
- `infra/k8s/platform/onlyoffice.yaml` 에 누락:
  - `ALLOW_PRIVATE_IP_ADDRESS=true` — 로컬에 있고 프로덕션에 없음
  - `JWT_INBOX_ENABLED=false`
  - `ONLYOFFICE_DOCS_PARAMS`
  - `postStart` lifecycle hook
- 로컬 `infra/local-dev/07-onlyoffice.yaml`에는 존재
- network-policy 주석(network-policy.yaml:304)이 "ALLOW_PRIVATE_IP_ADDRESS=true와 짝"이라고 명시

**H1과 관계**:
- H2는 **모든** 파일 타입 영향이라 Word-vs-Excel 차이를 설명하지는 못함
- 그러나 프로덕션만 Word/PPTX 실패 빈도가 특히 높다면 H2가 확대제 역할
- **독립 수정 대상** — H1 해결 여부와 무관하게 반영 필요

**검증**:
- `kubectl get deploy onlyoffice -n platform -o yaml` diff with `infra/local-dev/07-onlyoffice.yaml` env 섹션

**수정안**:
- `infra/k8s/platform/onlyoffice.yaml`에 로컬과 동일한 env + postStart 추가
- dry-run → diff → apply

### 🟡 H3: `_save_edited_file`의 dead `filetype` 파라미터 (api F2 + review + qa)

**증거**:
- `viewers.py:988` 콜백 핸들러가 `body.get("filetype")` 읽어서 전달
- `viewers.py:1118` `_save_edited_file(session, download_url, filetype)` — 함수 본문 전체에서 `filetype` 미사용
- 저장 경로는 항상 `session.file_path` 기반

**H1/H2와 관계**:
- **직접적 다운로드 실패 원인이 아님** — 저장 단계 이슈
- 그러나 OO DS가 `.doc` 열어서 `.docx`로 저장하는 케이스에서 파일 내용-경로 불일치(실제 .docx 바이트를 .doc 경로에 저장) 가능
- **데이터 무결성 이슈**로 별도 수정 대상

**수정안**:
- `_save_edited_file`에서 `filetype`이 `session.file_path` 확장자와 다르면 WARNING 로그 + (정책 결정) 허용 or 거부

## 기각된 가설

| 가설 | 기각 근거 | 제안자 |
|------|----------|--------|
| MIME_MAP에 office 확장자 없음 | 모든 오피스 타입 공통 → Word/PPTX만의 원인이 될 수 없음 | api F4, devops |
| JWT config key 순서 차이 | Python 3.7+ dict 삽입순 고정. documentType과 무관 | security KEY |
| 한글 파일명 NFC/NFD | 모든 타입 동일 경로. 이미 fallback 존재 | devops, web |
| 이미지 slim 변형(converter 누락) | onlyoffice/documentserver:8.2.2 CE = 표준, x2t 포함 | k8s |
| 클라이언트 ext 분기 | Hub UI에서 xlsx/docx/pptx 동일 URL/동일 encoding | web |

## 독립 보안 이슈 (Word/PPTX 버그와 별개, but urgent)

| # | 심각도 | 내용 | 파일:라인 |
|---|-------|------|----------|
| S1 | High | httpx `follow_redirects=True` 기본값 — redirect SSRF 경로 (IMDS 169.254.169.254) | viewers.py:1163 |
| S2 | High | config JWT `exp` 미설정 — 무기한 유효 | viewers.py:520 |
| S3 | Medium | SSRF allowlist 단축 hostname (`onlyoffice`, `documentserver`) | viewers.py:1090 |
| S4 | Medium | HTML editor 페이지 CSP `frame-ancestors` 누락 (clickjacking) | viewers.py:619, 745 |
| S5 | Medium | localhost rewrite 시 임의 포트 보존 | viewers.py:1136 |

## 아키텍처 의문 (Phase 4.5) — 해당 없음

P2-BUG 패치가 3회 반복됐지만 각 기원이 서로 다른 레이어(envelope format / DNS rewrite / kubectl→client)로, 동일 아키텍처의 반복 실패가 아님. 근본 구조 전환 필요 신호 없음.

## Phase 2~3 진행 제안

### 즉시 (사용자 승인 필요)
1. **H1 검증 instrumentation** — `/api/v1/viewers/file/` 에 1줄 로그 추가, 재현 테스트 후 로그 확인
2. **H2 prod manifest diff** — `kubectl get deploy onlyoffice -o yaml` 수집해 로컬과 diff

### H1 확정 시 Phase 4 수정 순서
1. failing test 먼저 작성 (`test_viewers_word_pptx.py` skeleton에 추가) — `.docx` 토큰 재사용 케이스
2. `_consume_file_token` → TTL 기반 N회 사용으로 변경 (minimal change)
3. qa가 작성한 skeleton에 회귀 테스트 포함
4. 커밋: `fix(P2-BUG4): OO DS 다중 fetch 지원 위해 file token TTL-based로 전환`

### 병행 수정 (H1과 독립)
- H2: platform manifest 반영 (devops 담당)
- H3: `filetype` mismatch 경고 로그 추가 (api 담당)
- S1~S5: 보안 취약점 별도 PR (security 담당)

## 결론

**가장 가능성 높은 단일 원인**: H1 (file token 1회용 소비 + OO DS 다중 fetch)
**Word/PPTX 실패 빈도 설명**: 크기(PPTX>DOCX>XLSX)와 재시도 확률 상관
**필수 전제 검증**: 실제 OO DS가 다중 fetch를 하는지 로그로 확인 (현재까지는 가설)

수정 진행 전에 사용자 승인 + H1 검증 로그 instrumentation이 필요하다.
