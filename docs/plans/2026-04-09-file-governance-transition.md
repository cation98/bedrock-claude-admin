# File Governance 전환 전략: file_share ACL → Governance Broker 통합

> **Issue**: #6  
> **작성일**: 2026-04-09  
> **상태**: 계획 수립

---

## 1. 현재 상태 (Current State)

두 개의 독립적인 파일 관리 시스템이 병렬로 운영 중이다.

### file_share ACL 시스템 (기존)
- **모델**: `SharedDataset` + `FileShareACL` (`file_share.py`)
- **역할**: 사용자 간 파일 공유 ACL 관리 (user/team 단위 권한 부여·회수)
- **API**: `/api/v1/files/datasets/*` — 데이터셋 CRUD, 공유 설정, SMS 인증, Pod 마운트 목록
- **특징**: `revoked_at` 기반 soft-delete, 소유권 검증, 감사 추적

### file_governance 시스템 (신규)
- **모델**: `GovernedFile` (`file_governance.py`)
- **역할**: Pod 에이전트가 스캔한 파일의 자동 분류(sensitive/normal/unknown), TTL 관리, 만료 추적
- **API**: `/api/v1/governance/*` — 스캔 보고, 대시보드, 파일 목록 (관리자 전용)
- **서비스**: `file_scanner.py` — 파일명 패턴, 조직, PII 콘텐츠 기반 자동 분류

### 현재 연결 지점
`file_share.py` 라우터가 이미 `GovernedFile`을 import하여 SMS 인증 시 민감 파일 여부를 확인한다 (`verify-access` endpoint, line 506-513). 이것이 유일한 cross-reference이다.

---

## 2. 리스크 분석 (Risk)

| 리스크 | 설명 | 심각도 |
|--------|------|--------|
| **이중 메타데이터** | 동일 파일이 `shared_datasets.file_path`와 `governed_files.file_path`에 별도로 존재. 파일명·크기 등이 불일치할 수 있음 | 높음 |
| **이중 정책 평가** | 공유 시 ACL 검사 + 민감도 검사가 서로 다른 테이블에서 수행. 분류가 누락되면 민감 파일이 SMS 인증 없이 공유될 수 있음 | 높음 |
| **TTL 사각지대** | `SharedDataset`에는 TTL/만료 개념이 없어, governance에서 expired된 파일이 여전히 공유 상태로 남을 수 있음 | 중간 |
| **감사 로그 분산** | 공유 이벤트는 application log, 분류 이벤트는 `file_audit_logs` 테이블. 통합 감사 불가 | 중간 |

---

## 3. 목표 상태 (Target State)

단일 **Governance Broker**가 파일의 전체 lifecycle을 관리한다:

```
파일 생성/업로드
  → Governance Broker (분류 + TTL 설정)
    → 공유 요청 시: ACL 검사 + 민감도 정책 평가 (단일 테이블)
    → 만료 시: 공유 ACL 자동 회수 + 파일 정리
```

- `GovernedFile`이 단일 진실 공급원(Single Source of Truth)
- 공유 ACL은 `GovernedFile`의 하위 개념으로 통합
- 분류·공유·만료가 하나의 트랜잭션 경로에서 처리

---

## 4. 마이그레이션 전략 (3 Phases)

### Phase A: Bridge (1주)

`GovernedFile`에 FK를 추가하여 `SharedDataset`과 연결. 양 시스템 공존.

**변경 사항:**
1. `GovernedFile`에 `shared_dataset_id = Column(Integer, ForeignKey("shared_datasets.id"), nullable=True)` 추가
2. 데이터셋 등록(`POST /datasets`) 시 자동으로 `GovernedFile` 레코드 생성 (classify_file 호출)
3. `verify-access`의 GovernedFile 조회를 FK 기반으로 변경 (filename 매칭 → FK join)

**검증**: 기존 API 동작 변화 없음. 신규 데이터셋만 bridge 적용.

### Phase B: Consolidate (2주)

새로운 공유 파일 flow가 governance broker를 먼저 통과하도록 변경.

**변경 사항:**
1. `POST /datasets` → governance 분류 완료 후에만 데이터셋 등록 허용
2. `GovernedFile.status != "active"`이면 공유 불가 (quarantine/expired 상태 차단)
3. TTL 만료 시 연관된 `FileShareACL`의 `revoked_at` 자동 설정
4. 감사 로그를 `file_audit_logs`로 통합

**검증**: 공유 요청이 governance 정책을 거치는지 end-to-end 테스트.

### Phase C: Deprecate (2주)

기존 `SharedDataset`/`FileShareACL` 제거, 데이터 마이그레이션.

**변경 사항:**
1. `GovernedFile`에 공유 ACL 필드 통합 (또는 별도 `governed_file_acl` 테이블 신설)
2. 기존 데이터 마이그레이션 SQL 실행
3. `file_share.py` 라우터의 endpoint를 governance 라우터로 redirect 또는 이전
4. `SharedDataset`, `FileShareACL` 모델 및 테이블 삭제

---

## 5. 데이터 마이그레이션 SQL

### Phase A: GovernedFile에 FK 컬럼 추가
```sql
ALTER TABLE governed_files
  ADD COLUMN shared_dataset_id INTEGER REFERENCES shared_datasets(id);

-- 기존 데이터 연결 (file_path 기반 매칭)
UPDATE governed_files gf
SET shared_dataset_id = sd.id
FROM shared_datasets sd
WHERE gf.file_path = sd.file_path
  AND gf.username = sd.owner_username;
```

### Phase C: SharedDataset → GovernedFile 마이그레이션
```sql
-- 1. GovernedFile에 없는 SharedDataset 데이터 이관
INSERT INTO governed_files (username, filename, file_path, file_type, file_size_bytes,
                            classification, status, created_at, updated_at)
SELECT sd.owner_username,
       sd.dataset_name,
       sd.file_path,
       sd.file_type,
       sd.file_size_bytes,
       'unknown',      -- 분류 미완료 상태로 이관, 추후 재스캔
       'active',
       sd.created_at,
       sd.updated_at
FROM shared_datasets sd
WHERE NOT EXISTS (
    SELECT 1 FROM governed_files gf
    WHERE gf.file_path = sd.file_path
      AND gf.username = sd.owner_username
);

-- 2. ACL 데이터를 새 테이블로 이관 (governed_file_acl 신설 시)
INSERT INTO governed_file_acl (governed_file_id, share_type, share_target,
                                granted_by, granted_at, revoked_at)
SELECT gf.id, acl.share_type, acl.share_target,
       acl.granted_by, acl.granted_at, acl.revoked_at
FROM file_share_acl acl
JOIN shared_datasets sd ON sd.id = acl.dataset_id
JOIN governed_files gf ON gf.file_path = sd.file_path
                       AND gf.username = sd.owner_username;

-- 3. 정리 (Phase C 완료 확인 후)
-- DROP TABLE file_share_acl;
-- DROP TABLE shared_datasets;
```

---

## 6. API 마이그레이션 매핑

| 기존 Endpoint | 신규 Endpoint | 비고 |
|--------------|--------------|------|
| `POST /files/datasets` | `POST /governance/files` | 등록 + 자동 분류 통합 |
| `GET /files/datasets/my` | `GET /governance/files?owner=me` | 필터 파라미터로 대체 |
| `GET /files/datasets/shared` | `GET /governance/files?shared_with=me` | ACL 기반 필터 |
| `POST /files/datasets/{name}/share` | `POST /governance/files/{id}/share` | name → id 기반으로 변경 |
| `DELETE /files/datasets/{name}/share/{acl_id}` | `DELETE /governance/files/{id}/share/{acl_id}` | 동일 |
| `GET /files/datasets/{name}/share` | `GET /governance/files/{id}/share` | 동일 |
| `GET /files/shared-mounts/{username}` | `GET /governance/mounts/{username}` | 동일 기능 |
| `POST /files/datasets/{name}/verify-access` | `POST /governance/files/{id}/verify-access` | 통합 정책 평가 |
| `GET /files/teams` | 변경 없음 | 조직 조회는 file 시스템과 무관 |
| `GET /governance/scan-report` | 변경 없음 | 유지 |
| `GET /governance/dashboard` | 변경 없음 | 공유 통계 항목 추가 |

---

## 7. Breaking Changes

### 클라이언트 업데이트 필요 사항

1. **Hub UI (웹 터미널)**
   - 파일 공유 UI가 `/api/v1/files/datasets/*` 호출 → `/api/v1/governance/files/*`로 변경
   - 데이터셋 식별자가 `name` → `id` (integer)로 변경

2. **Pod 에이전트 (container-image)**
   - `scan-report` endpoint는 변경 없음 (호환성 유지)
   - 데이터셋 등록 flow가 변경되므로 파일 업로드 후 호출 endpoint 확인 필요

3. **Admin Dashboard**
   - `/governance/dashboard`에 공유 관련 통계 필드 추가 (하위 호환)
   - `/governance/files` 응답에 ACL 정보 포함 (필드 추가)

4. **Deprecation 일정**
   - Phase B 완료 후 기존 `/files/datasets/*` endpoint에 `Deprecation` 헤더 추가
   - Phase C 시작 전 최소 2주간 deprecation 경고 기간 운영
   - Phase C 완료 후 기존 endpoint 제거 (404 반환)

### 하위 호환성 유지 방안
- Phase B 동안 기존 endpoint를 proxy로 유지 (내부적으로 governance broker 호출)
- Response schema에 신규 필드 추가만 허용 (기존 필드 제거 금지)
- `X-Deprecated: true` 헤더로 클라이언트에 마이그레이션 유도
