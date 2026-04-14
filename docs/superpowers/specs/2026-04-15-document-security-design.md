# 문서 보안 / DRM 시스템 설계 스펙

**작성일**: 2026-04-15  
**작성자**: cation98 (최종언)  
**상태**: 승인됨  
**관련 ADR**: mindbase `adr-document-security-drm-evolution` (project: bedrock-ai-agent)  
**관련 이슈**: cation98/safety-management-system#35

---

## 1. 개요

사용자가 업로드한 모든 문서 파일에 대해 서비스 전용 암호화, 다운로드 금지, 반출 승인 시스템을 구축한다.

### 1.1 보호 대상

- Claude Code 터미널(Pod) 내 파일 (소스코드, 데이터, 문서)
- Claude 채팅(OpenWebUI)에 업로드된 첨부파일 (PDF, Excel, 이미지 등)
- S3 Vault에 저장된 민감 문서
- 포털/Admin에서 업로드된 공식 문서

### 1.2 보호 목표

| 목표 | 내용 |
|------|------|
| **실수/과실 방지** | 카카오톡·이메일 무심코 공유, USB 저장 차단 |
| **내부자 위협 방지** | 고의적 파일 반출 경로 차단 |
| **규정 준수** | 접근 이력 + 반출 승인 이력을 감사 증거로 보존 |

### 1.3 핵심 원칙

- **서버에서만 복호화**: 클라이언트에 원본 바이트 전달 금지
- **뷰어 렌더링만 전달**: 파일 내용은 HTML/이미지로 변환하여 전달
- **반출은 반드시 승인**: 목적별 2단계 승인 + 워터마크 + 시간제한 토큰
- **내부 작업은 자유**: 서비스 내 코딩·편집·분석은 제한 없음

---

## 2. 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                    사용자 진입점                              │
│  Claude Code 터미널(Pod)  │  OpenWebUI  │  Portal/Admin     │
└──────────────┬────────────┴──────┬──────┴──────┬────────────┘
               │                  │             │
               ▼                  ▼             ▼
┌─────────────────────────────────────────────────────────────┐
│              auth-gateway (파일 보안 레이어)                  │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ File Access  │  │  DEK Crypto  │  │  Export Workflow  │  │
│  │ Controller   │  │  Service     │  │  (반출 승인)      │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                 │                   │             │
│  ┌──────▼───────────────────────┐    ┌────────▼─────────┐  │
│  │       뷰어 라우터             │    │  Watermark Engine │  │
│  │  Office → OnlyOffice         │    │  (PDF/Office)     │  │
│  │  Code  → Monaco Viewer       │    └──────────────────┘  │
│  │  Image → 갤러리 렌더링        │                          │
│  └──────────────────────────────┘                          │
└───────────────────────────┬─────────────────────────────────┘
                            │
               ┌────────────▼────────────┐
               │    S3 Vault (암호화 저장) │
               │  원본: AES-256-GCM(DEK)  │
               │  DEK:  KMS Envelope      │
               └─────────────────────────┘
```

---

## 3. Phase 1: 뷰어 통제

### 3.1 파일 유형별 뷰어 라우팅

모든 파일 접근은 `GET /api/v1/files/view/{vault_id}` 단일 엔드포인트로 통일한다.

| 파일 유형 | 뷰어 | 방식 |
|----------|------|------|
| `.pdf`, `.docx`, `.xlsx`, `.pptx` | OnlyOffice Document Server | JWT 토큰 + 기능 제한 |
| `.py`, `.js`, `.ts`, `.sh`, `.yaml` 등 | Monaco Code Viewer | 서버사이드 렌더링 |
| `.jpg`, `.png`, `.gif`, `.svg` | Image Strip & Serve | Exif 메타데이터 제거 후 인라인 |
| 그 외 바이너리 | — | "열람 불가" 안내 페이지 |

### 3.2 OnlyOffice 설정

```json
{
  "document": {
    "permissions": {
      "download": false,
      "print": false,
      "copy": false,
      "edit": false
    }
  },
  "editorConfig": {
    "customization": {
      "forcesave": false,
      "chat": false,
      "help": false
    }
  }
}
```

OnlyOffice 접근은 auth-gateway가 발급하는 단기 JWT로만 허용한다. 직접 URL 접근 차단.

### 3.3 HTTP 응답 헤더 정책

```python
headers = {
    "Content-Disposition": "inline",           # attachment 금지
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store, no-cache",
    "Content-Security-Policy": "default-src 'self'",
}
```

S3 Presigned URL을 통한 직접 접근 폐지. 모든 파일 접근은 auth-gateway 경유 강제.

### 3.4 터미널(Pod) 제어

| 제어 항목 | 구현 방법 |
|----------|----------|
| EFS 마운트 실행 방지 | `noexec` 마운트 옵션 추가 |
| 외부 파일 업로드 차단 | NetworkPolicy egress 화이트리스트 적용 |
| 다운로드 UI 제거 | 터미널 웹 인터페이스에서 파일 다운로드 버튼 제거 |

**NetworkPolicy egress 허용 목록** (이 외 모두 차단):
- npm registry, PyPI, GitHub (개발 도구)
- AWS Bedrock, ECR (서비스 필수)
- RDS, Redis, EFS 엔드포인트
- auth-gateway 내부 통신

### 3.5 OpenWebUI 제어

- 파일 첨부 후 다운로드 버튼 제거 → 뷰어 URL로 대체
- 업로드된 파일은 서버에서만 처리, 클라이언트로 원본 반환 없음

---

## 4. Phase 2: DEK 기반 서비스 전용 암호화

### 4.1 Envelope 암호화 흐름

**업로드**:
```
원본 파일
  → DEK 생성 (AES-256-GCM, 파일마다 고유, 32바이트 랜덤)
  → 파일 암호화 (AES-256-GCM + IV)
  → DEK → AWS KMS Encrypt API → Encrypted DEK
  → 암호화된 파일 → S3 저장
  → Encrypted DEK + IV → DB (file_dek 테이블) 저장
```

**열람**:
```
DB에서 Encrypted DEK 조회
  → KMS Decrypt API → DEK (서버 메모리)
  → S3에서 암호화된 파일 조회
  → DEK로 복호화 (서버 메모리)
  → 뷰어 렌더링 → HTML/이미지만 클라이언트 전달
  → 메모리에서 DEK + 평문 즉시 삭제 (del)
```

### 4.2 신규 DB 테이블: `file_dek`

```sql
CREATE TABLE file_dek (
    id              SERIAL PRIMARY KEY,
    vault_id        VARCHAR(64) UNIQUE NOT NULL,   -- S3 파일 식별자
    owner_username  VARCHAR(50) NOT NULL,
    encrypted_dek   TEXT NOT NULL,                 -- KMS 암호화된 DEK (base64)
    iv              VARCHAR(64) NOT NULL,           -- AES-GCM IV (base64)
    kms_key_id      VARCHAR(200) NOT NULL,          -- 사용된 KMS 키 ARN
    algorithm       VARCHAR(20) DEFAULT 'AES-256-GCM',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    rotated_at      TIMESTAMPTZ                    -- 키 교체 이력
);
```

### 4.3 기존 파일 마이그레이션 전략

```
신규 업로드:   DEK 암호화 즉시 적용
기존 S3 파일:  Phase 1 뷰어 통제로 우선 보호
               → 야간 배치 작업으로 순차 재암호화
               → 완료 후 file_dek 레코드 생성
```

### 4.4 키 보안 원칙

| 원칙 | 구현 |
|------|------|
| DEK 평문은 메모리에서만 존재 | 함수 종료 시 즉시 `del dek_bytes` |
| S3 직접 접근 불가 | Bucket Policy: auth-gateway IAM Role만 허용 |
| KMS 호출 감사 | CloudTrail → Phase 4 이상탐지 연동 |
| DEK 주기적 교체 | 6개월마다 KMS `ReEncrypt` API |
| Presigned URL 폐지 | 모든 접근 auth-gateway 경유 강제 |

---

## 5. Phase 3: 반출 승인 워크플로 + 워터마크 엔진

### 5.1 반출 목적별 흐름

```
[사용자 반출 요청]
        │
        ▼
   pending (팀장 1차 승인 대기)
        │
        ▼
   step1_approved (보안관리자 2차 승인 대기)
        │
   ┌────┴──────────────────────┐
   │ 열람용 (readonly)          │ 편집용 (editable)
   │ 2단계 승인으로 완료         │ 보안관리자 추가 확인
   └────┬──────────────────────┘
        │ approved
        ▼
   반출 토큰 발급 + 워터마크 파일 생성
        │
        ▼
   downloaded → expired (자동 만료)
        또는
   rejected (어느 단계에서나)
```

### 5.2 신규 DB 테이블: `export_requests`

```sql
CREATE TABLE export_requests (
    id               SERIAL PRIMARY KEY,
    request_no       VARCHAR(30) UNIQUE NOT NULL,   -- EXP-20260415-0001
    requester        VARCHAR(50) NOT NULL,
    vault_id         VARCHAR(64) NOT NULL,
    purpose          VARCHAR(10) NOT NULL,           -- 'readonly' | 'editable'
    reason           TEXT NOT NULL,

    status           VARCHAR(20) DEFAULT 'pending',
    -- pending | step1_approved | approved | downloaded | expired | rejected

    approver1        VARCHAR(50),
    approved1_at     TIMESTAMPTZ,
    approver2        VARCHAR(50),
    approved2_at     TIMESTAMPTZ,
    rejection_reason TEXT,

    export_token     VARCHAR(128) UNIQUE,
    token_expires_at TIMESTAMPTZ,                   -- 기본 24시간
    max_downloads    INT DEFAULT 1,
    download_count   INT DEFAULT 0,
    downloaded_at    TIMESTAMPTZ,

    watermark_id     VARCHAR(64),                   -- 추적용 고유 식별번호
    export_format    VARCHAR(10),                   -- 'pdf' | 'original'

    created_at       TIMESTAMPTZ DEFAULT NOW()
);
```

### 5.3 워터마크 엔진

**열람용 (PDF 변환)**:
```
원본 파일
  → OnlyOffice Document Server: 파일 → PDF 변환
  → pikepdf: 모든 페이지 반투명 대각선 워터마크 삽입
    내용: "[반출] {사번} | {날짜} | 반출번호: {EXP-...}"
  → 단기 다운로드 토큰과 함께 제공
```

**편집용 (원본 포맷)**:

| 포맷 | 워터마크 방법 | 라이브러리 |
|------|-------------|----------|
| `.xlsx` | 모든 시트 헤더 + 배경 이미지 | openpyxl |
| `.docx` | 모든 섹션 헤더 워터마크 | python-docx |
| `.pptx` | 슬라이드 마스터에 삽입 | python-pptx |

> 편집용 워터마크는 제거 가능하지만 추적번호가 남으므로 유출 시 소급 추적 가능.

### 5.4 반출 토큰 구조

```python
# JWT 페이로드
{
    "export_id": 42,
    "request_no": "EXP-20260415-0001",
    "requester": "N1102359",
    "vault_id": "abc123def456",
    "expires_at": "2026-04-16T23:59:59Z",
    "max_downloads": 1,
    "ip_bound": "10.x.x.x"   # 승인 시점 IP 바인딩
}
# HS256 서명, 서버 비밀키로 검증
# 사용 시 download_count++ → max_downloads 초과 시 즉시 만료
```

### 5.5 Admin Dashboard 신규 페이지: `/apps/export-requests`

- 반출 요청 목록 (상태별 필터: 대기/승인/완료/반려)
- 1차·2차 승인 버튼 (역할별 노출)
- 반출 이력 다운로드 (감사용 CSV)

---

## 6. Phase 4: 이상탐지 + 감사

### 6.1 `file_audit_logs` 테이블 확장

기존 컬럼 유지 + 추가:
```sql
ALTER TABLE file_audit_logs ADD COLUMN
    export_request_no VARCHAR(30),
    session_id        VARCHAR(128),
    vault_id          VARCHAR(64),
    file_size_bytes   INT,
    user_agent        TEXT,
    result            VARCHAR(10)   -- 'success' | 'denied' | 'error'
;
```

새 액션 유형:
```
VIEW        열람 (뷰어 접근)
EXPORT_REQ  반출 요청
EXPORT_APR  반출 승인
EXPORT_REJ  반출 반려
EXPORT_DL   반출 다운로드
POLICY_DENY 정책 차단
```

### 6.2 이상탐지 패턴

`file-security-worker` (신규, usage-worker 패턴 동일):

```
파일 접근 이벤트 → Redis Stream: stream:file_events
                         │
                   file-security-worker
                         │
         ┌───────────────┼──────────────────┐
         ▼               ▼                  ▼
    시간 패턴         볼륨 패턴          반출 패턴

시간:  새벽 00~05시 파일 접근 → HIGH 즉시 알림

볼륨:  동일 사용자 1시간 내 파일 10개 이상 → MEDIUM 알림
       동일 파일 1시간 내 5회 이상 접근 → MEDIUM 알림

반출:  반출 거절 후 즉시 재요청 3회 이상 → HIGH 알림
       반출 토큰 IP ≠ 승인 시점 IP → 다운로드 차단 + HIGH 알림
       만료 토큰 접근 시도 → LOW 기록
```

### 6.3 알림 채널

| 위험도 | 채널 | 시점 |
|--------|------|------|
| HIGH | Telegram Bot + Admin Dashboard 배너 | 즉시 |
| MEDIUM | Admin Dashboard 알림 | 즉시 |
| LOW | 감사 로그 기록 | 일간 리포트 |

### 6.4 기존 `/audit` 페이지 확장

파일 보안 현황 섹션 추가:
- 오늘 열람/차단/반출요청 건수 요약
- 이상 탐지 목록 (사유, 대상자, 시각)
- 반출 이력 (최근 N건)

---

## 7. 구현 일정 (권장 순서)

| Phase | 내용 | 예상 기간 | 이유 |
|-------|------|----------|------|
| **Phase 1** | 뷰어 통제 | 2~3주 | 가장 빠른 효과, 기존 OnlyOffice 활용 |
| **Phase 3** | 반출 승인 워크플로 | 3~4주 | 비즈니스 가치 선 확보 |
| **Phase 2** | DEK 암호화 | 4~5주 | 기존 파일 재암호화 배치 포함, 신중히 |
| **Phase 4** | 이상탐지 | 2~3주 | Phase 1~3 완료 후 |

---

## 8. 현재 인프라 현황

| 구성요소 | 상세 |
|---------|------|
| S3 Vault | `bedrock-claude-s3-vault-680877507363` (KMS SSE 적용) |
| KMS Key | `bc47d786-64b9-42ae-8d03-58374253dd23` (ap-northeast-2) |
| OnlyOffice | `onlyoffice-79fc79dff6-wmjs9` (claude-sessions ns, Running) |
| File Audit | `file_audit_logs` 테이블 (upload/classify/delete/share/access 등) |
| File Governance | 파일 분류/스캔 시스템 (GovernedFile 모델) |
| Usage Worker | Redis Stream 기반 비동기 워커 패턴 (file-security-worker 참고) |

---

## 9. 장기 진화 방향 (ADR 참조)

단기(본 스펙)는 기존 인프라 위에 레이어 추가 방식이나, 아래 트리거 발생 시 **독립 DRM 마이크로서비스**로 전환:

- 파일 API 부하가 auth-gateway 전체의 30% 초과
- 반출 요청 건수 월 100건 이상
- 외부 감사/컴플라이언스 격리 요건 발생

상세 ADR: mindbase `adr-document-security-drm-evolution` (project: bedrock-ai-agent)
