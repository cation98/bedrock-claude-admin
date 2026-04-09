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
