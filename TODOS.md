# TODOS — Bedrock AI Platform

> 마지막 업데이트: 2026-04-09
> 작업자: cation98
> 이 문서를 읽고 다른 머신에서 작업을 이어갈 수 있도록 배포이력과 함께 정리.

---

## 배포 이력 (2026-04-09)

### 커밋 히스토리
```
7ba3030 notice: 시스템 개편 공지 배너 (로그인 화면)
fd8aee6 feat: 통합 보안 + 데이터 거버넌스 플랫폼 (Phase 1-4)  ← PR #4 squash merge
063eb77 feat: Hub 파일 탐색기 + 배포 인프라 개선              ← 별도 작업 (다른 세션)
```

### 배포된 것
| 구성요소 | 상태 | 이미지/매니페스트 | 비고 |
|---------|------|-----------------|------|
| auth-gateway | Running (2 replicas, platform ns) | ECR `bedrock-claude/auth-gateway:latest` | 보안 수정 6건 + 거버넌스 API + S3 Vault API + 뷰어 API |
| OnlyOffice | Running (1 replica, claude-sessions ns) | `onlyoffice/documentserver:latest` | Community Edition, JWT, ClusterIP |
| claude-code-terminal | ECR 푸시 완료 | ECR `bedrock-claude/claude-code-terminal:latest` | secure-cli + scanner-agent + OWASP + robots.txt |
| NetworkPolicy | 적용됨 (7개) | `infra/k8s/platform/network-policy.yaml` | default-deny + 선택적 허용 |
| RBAC | secrets 권한 추가 | `infra/k8s/platform/rbac.yaml` | X-Pod-Token Secret 관리용 |

### 배포되지 않은 것 (인프라 프로비저닝 필요)
| 구성요소 | 이유 | Terraform 파일 |
|---------|------|---------------|
| ElastiCache Redis | Terraform 미적용 | `infra/terraform/elasticache.tf` 생성 필요 |
| S3 Vault 버킷 + KMS 키 | Terraform 미적용 | `infra/terraform/s3-vault.tf` 생성 필요 |
| Squid 이그레스 프록시 | Phase 5로 연기 | `infra/terraform/squid-proxy.tf` 생성 필요 |

### DB 상태
- 새 테이블 (`governed_files`, `file_audit_logs`, `sqlcipher_keys`) + 새 컬럼 (`pod_token_hash`) → SQLAlchemy `create_all`로 자동 생성
- Alembic 정식 migration 미생성 → Issue #11

### 로그인 화면
- 시스템 개편 공지 배너 활성 중 → Issue #14

---

## ✅ 이미 해결된 항목 (이전 TODOS에서 이관)

| 항목 | 해결 방법 | 커밋 |
|------|----------|------|
| TEST* SSO/2FA 바이패스 | `allow_test_users` env flag로 게이팅 (기본 False) | Task #4 in fd8aee6 |
| shared-mounts API 인증 | `get_current_user_or_pod` + 소유자/admin 검증 추가 | Task #2 in fd8aee6 |
| NetworkPolicy 미적용 | 7개 정책 `kubectl apply` 완료 | 2026-04-09 배포 |

---

## 미완료 작업 (GitHub Issues #5~#16)

### 🔴 High Priority

#### #5 — EKS 50명 상시 운용 사이징 재계산
- **문제**: 현재 4 x m5.xlarge(64Gi)로 50 Pod(75Gi 요청) 감당 불가
- **배경**: 전사 운영 50명으로 목표 변경
- **작업 위치**: `infra/terraform/`, `auth-gateway/app/models/infra_policy.py`
- **선행조건**: 없음

#### #7 — Redis thread-safety (threading.Lock)
- **문제**: `auth-gateway/app/core/redis_client.py` `get_redis()` 모듈 레벨 변수 race condition
- **수정**: `threading.Lock` + double-checked locking
- **선행조건**: 없음

#### #8 — Redis rate limiter sliding window
- **문제**: `container-image/app-runtime/security_middleware.py` fixed-window → 경계에서 2x 허용
- **수정**: Redis sorted set sliding window
- **선행조건**: #7

#### #9 — Redis stale client reference
- **문제**: `RateLimiter`가 Redis 클라이언트 캐싱 → 장애 후 영구 fallback
- **수정**: `check()`에서 매번 `get_redis()` 호출
- **선행조건**: #7

#### #11 — Alembic DB migration
- **테이블**: `governed_files`, `file_audit_logs`, `sqlcipher_keys` + `terminal_sessions.pod_token_hash`
- **수정**: `alembic revision --autogenerate`
- **선행조건**: 없음

### 🟡 Medium Priority

#### #6 — file_share ACL → 거버넌스 브로커 전환 전략
- **문제**: `SharedDataset`/`FileShareACL`과 `GovernedFile` 이중 메타데이터
- **파일**: `auth-gateway/app/models/file_share.py`, `auth-gateway/app/models/file_governance.py`

#### #10 — Scheduler lock Lua script ownership
- **문제**: `release_scheduler_lock` plain DELETE → 타 레플리카 락 삭제 가능
- **파일**: `auth-gateway/app/core/redis_client.py`
- **선행조건**: Redis 배포 후

#### #12 — delete_all_pods Secret 정리
- **문제**: orphan `pod-token-*` Secret 축적
- **파일**: `auth-gateway/app/services/k8s_service.py`

#### #15 — OnlyOffice AGPL 법무팀 검토
- **작업**: 법무팀에 AGPL v3 사내 사용 검토 요청

#### S3 Vault 환경변수 설정 (미등록 이슈)
- **문제**: `s3_vault_bucket`, `s3_vault_kms_key_id` env var 미설정
- **작업**: S3 버킷 + KMS 키 생성 (Terraform) → env var 추가
- **코드**: `auth-gateway/app/services/s3_vault.py` (배포됨), `container-image/secure-cli/` (배포됨)

### 🟢 Low Priority

#### #13 — /internal-heartbeat Token 검증
- **파일**: `auth-gateway/app/routers/sessions.py:659-686`

#### #14 — 시스템 공지 배너 제거
- **파일**: `auth-gateway/app/static/login.html` (`<!-- 시스템 공지 배너 -->` 블록)
- **배포**: 이미지 리빌드 + ECR 푸시 + rollout restart 필요

#### #16 — Phase 5 (AWS Network Firewall + Bedrock logging)

#### PDF 뷰어 실제 구현 (미등록 이슈)
- **현재**: `container-image/viewers/pdf-viewer/README.md`만 존재
- **작업**: react-pdf 기반 실제 뷰어 컴포넌트 구현

#### 스킬 추천 엔진 + 평점/리뷰 (미등록 이슈)
- **현재**: Hub 스킬 스토어 인기순/최신순만 구현
- **작업**: 개인화 추천 + 사용자 리뷰 시스템

---

## 다른 머신에서 작업 이어가기

### 환경 설정
```bash
# 1. 리포지토리
git clone git@github.com:cation98/bedrock-ai-agent.git && cd bedrock-ai-agent

# 2. AWS 인증
aws sts get-caller-identity  # 확인, 필요 시 aws sso login

# 3. EKS 연결
aws eks update-kubeconfig --name bedrock-claude-eks --region ap-northeast-2
kubectl get pods -n platform  # auth-gateway 확인
kubectl get pods -n claude-sessions -l app=onlyoffice  # OnlyOffice 확인

# 4. auth-gateway 개발환경
cd auth-gateway && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# ⚠️ .env 파일에 JWT_SECRET_KEY 필수 (기본값 제거됨!)

# 5. 테스트
python -m pytest tests/ -v
```

### 배포 방법
```bash
ECR="680877507363.dkr.ecr.ap-northeast-2.amazonaws.com"
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin "$ECR"

# auth-gateway
cd auth-gateway
docker build --platform linux/amd64 -t "$ECR/bedrock-claude/auth-gateway:latest" .
docker push "$ECR/bedrock-claude/auth-gateway:latest"
kubectl rollout restart deployment/auth-gateway -n platform

# container-image
cd container-image
docker build --platform linux/amd64 -t "$ECR/bedrock-claude/claude-code-terminal:latest" .
docker push "$ECR/bedrock-claude/claude-code-terminal:latest"

# K8s manifests
kubectl apply -f infra/k8s/platform/
```

### 주요 파일 맵
```
auth-gateway/app/
├── core/security.py         ← X-Pod-Token 인증
├── core/redis_client.py     ← Redis (Issue #7,#8,#9)
├── core/config.py           ← JWT_SECRET_KEY 필수!
├── core/scheduler.py        ← TTL + SETNX 분산 락
├── models/file_governance.py ← GovernedFile
├── models/file_audit.py     ← FileAuditLog
├── routers/file_governance.py ← 거버넌스 API
├── routers/secure_files.py  ← S3 Vault API
├── routers/viewers.py       ← OnlyOffice + PDF
├── routers/file_share.py    ← SMS 공유 인증
├── services/file_scanner.py ← 민감파일 분류
├── services/s3_vault.py     ← S3+KMS
├── services/sqlcipher_service.py ← SQLCipher
├── static/login.html        ← 공지 배너 (제거 필요)
└── tests/                   ← ~154 테스트

container-image/
├── secure-cli/              ← secure-put, secure-get, secure-cleanup
├── scripts/file-scanner-agent.py
├── config/CLAUDE.md         ← Pod OWASP 지시어
├── config/robots.txt
└── viewers/                 ← OnlyOffice config, PDF viewer

infra/k8s/platform/
├── network-policy.yaml      ← 7개 정책 (배포됨)
├── onlyoffice.yaml          ← OnlyOffice (배포됨)
├── rbac.yaml                ← secrets 권한 (배포됨)
└── efs-access-points.yaml   ← 템플릿 (동적 생성 미구현)

DESIGN.md                    ← 디자인 시스템 (Geist+Pretendard)
```

---

## Open WebUI 통합 허브 확장 — 엔지니어링 리뷰 산출 TODOs (2026-04-12)

> 설계 문서: `~/.gstack/projects/cation98-bedrock-ai-agent/cation98-main-design-20260412-133106.md`
> 테스트 플랜: `~/.gstack/projects/cation98-bedrock-ai-agent/cation98-main-eng-review-test-plan-20260412-143159.md`

### 🔴 Phase 0 Blockers (Phase 1 착수 전 반드시 해결)

#### #17 — DocSpace MCP ↔ Document Server 호환성 PoC
- **무엇을**: `ONLYOFFICE/docspace-mcp` 서버를 기존 Document Server Community Edition 엔드포인트(claude-sessions ns)에 붙여 동작 여부 검증.
- **왜**: 설계 전제 "OnlyOffice 재사용"의 성립 여부 결정. 불호환이면 DocSpace 전환 또는 Phase 1 스코프에서 OnlyOffice 통합 제거.
- **어떻게**: 별도 테스트 네임스페이스에 DocSpace MCP 배포 → 기존 Document Server JWT로 인증 시도 → `get_folder_content`, `upload_file` 기본 tool 호출 → 결과 문서.
- **Depends on**: 없음
- **Pros**: 설계 전제 검증. 리스크 제거.
- **Cons**: 1일 작업.
- **Context**: DocSpace MCP는 DocSpace 제품군 전용으로 설계됨. Document Server는 편집 엔진만 제공하는 다른 제품. T1-T9 편집 기능은 Document Server를 직접 사용하므로 DocSpace 전환 시 재작업 가능성.

#### #18 — Redis Stream + usage-worker 설계·배포
- **무엇을**: ElastiCache Redis에 `stream:usage_events` Stream 생성, `usage-worker` Python Deployment 신설(consumer group).
- **왜**: 사용량 INSERT 동기 실행이 2,000명 규모 병목. Request path에서 분리.
- **어떻게**: (1) Terraform으로 ElastiCache Redis 배포 (TODOS #5 해결 과정에서) (2) `usage-worker/` 디렉토리 신설(FastAPI/Celery 아닌 순수 Python consumer) (3) 배치 10건 또는 1초 단위 RDS INSERT.
- **Depends on**: TODOS #5 (EKS/ElastiCache), #11 (Alembic)
- **Pros**: p99 latency 1ms로 감소. RDS 부하 분산.
- **Cons**: Worker 장애 시 Stream 적체. PVC WAL로 완화.

#### #20 — Console Pod AWS SDK → HTTP proxy 마이그레이션
- **무엇을**: User Pod의 `ANTHROPIC_BASE_URL`을 Bedrock AG 내부 endpoint로 고정. AWS SDK 직접 호출 경로 차단(NetworkPolicy egress AWS Bedrock 차단).
- **왜**: 사용량 추적 단일화. User attribution을 JWT 기반으로 통일.
- **어떻게**: (1) Bedrock AG에 Anthropic 호환 엔드포인트 추가(모델 ID 매핑) (2) container-image entrypoint.sh에 환경변수 설정 (3) NetworkPolicy `egress` 규칙 추가.
- **Depends on**: Bedrock AG 배포(Phase 0 내 선행)
- **Pros**: usage_events 일관성 보장. Console 사용량 가시성 확보.
- **Cons**: Claude Code CLI가 HTTP proxy를 정상 인식하는지 사전 검증 필요.

#### #24 — CLAUDE.md 시스템 노드 제약 수정
- **무엇을**: `system-node-large` nodegroup max 3 → max 6. 또는 ingress-nginx를 별도 `ingress-workers` nodegroup으로 분리.
- **왜**: Open WebUI WebSocket 트래픽(500 concurrent long-lived)이 현재 제약(max 3)에서 수용 불가.
- **어떻게**: (1) CLAUDE.md Infrastructure Design Constraints 섹션 수정 (2) Terraform nodegroup 스펙 변경 (3) ingress-nginx anti-affinity 설정 재검토.
- **Depends on**: 없음
- **Pros**: 용량 절벽 해결.
- **Cons**: 비용 증가(노드 추가). 월 ~$150 추정.

### 🟡 Phase 1 작업

#### #19 — JWT refresh 401 redirect 페이지
- **무엇을**: `auth-gateway/app/static/auth-expired.html` 신설 + `chat.skons.net/auth/expired` 라우트 매핑.
- **왜**: Open WebUI 코어 수정 없이 브라우저 레벨 refresh 구현.
- **어떻게**: Open WebUI 401 응답 시 nginx error_page 지시어로 정적 HTML 서빙 → JS가 `portal.html?refresh=1` 리디렉트 → Hub가 auth-gateway refresh 호출 → 새 JWT 쿠키 세팅 → 원래 경로 복귀.
- **Depends on**: #17, #18, #24 완료
- **Pros**: Open WebUI 업스트림 영향 zero.
- **Cons**: 사용자 경험상 짧은 리디렉트 노출.

#### #23 — Parallel UIs 사용률 instrumentation
- **무엇을**: user_id × source(webchat|console) 주간/월간 활성 사용자 리포트 Admin Dashboard 페이지.
- **왜**: 설계의 parallel UIs exit criteria 평가 데이터 확보. 분기별 review용.
- **어떻게**: `usage_events` 집계 쿼리 + admin-dashboard `/analytics/ui-split` 페이지.
- **Depends on**: #18 완료
- **Pros**: 데이터 기반 console 폐기 결정 가능.
- **Cons**: 3개월 이상 관측 필요.

### 🟢 Phase 2 작업

#### #21 — Open WebUI 데이터 export 스크립트
- **무엇을**: `/ops/export-{chats,skills,usage,audit}.py` 스크립트 작성.
- **왜**: Open WebUI 벤더 락인 완화. 향후 LibreChat·Cherry Studio 등 마이그레이션 옵션 확보.
- **어떻게**: Postgres/RDS SELECT → JSONL/CSV/Parquet export. 개인 요청 시 90일 이내 대화 추출.
- **Depends on**: Phase 1 안정 운영 30일
- **Pros**: 조달 리스크 완화. ISMS-P 데이터 권리 대응.

#### #22 — Skills governance 스키마 및 승인 워크플로
- **무엇을**: `skills` 테이블 `approval_status`, `approved_by`, `version`, `skills_history` 테이블 추가. Admin Dashboard 스킬 승인 UI.
- **왜**: PIPA·ISMS-P 요구사항. SoD(직무분리) 확보.
- **어떻게**: Alembic migration + Admin UI 페이지 + 승인/반려 이메일 알림.
- **Depends on**: #11 Alembic 정식화, Phase 1 완료
- **Pros**: 컴플라이언스 충족.

### Phase Gate 요약
- Phase 0 Blockers: #5, #11, #17, #18, #20, #24 (총 6건)
- Phase 1 보완: #19, #23
- Phase 2 확장: #21, #22
