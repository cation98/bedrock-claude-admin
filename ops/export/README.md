# ops/export — Bedrock AI Platform 데이터 Export 도구

ISMS-P 제35조 데이터 주체 권리 대응 + 운영 분석용 export 스크립트 모음.
Phase 1a (2026-04-12) 신설.

## 스크립트

| 파일 | 대상 테이블 | 포맷 | 기본 범위 |
|------|-----------|------|----------|
| `chats.py` | Open WebUI `chat` | JSONL | 사용자별 90일 |
| `skills.py` | Platform `skills` | CSV | approval_status 필터 |
| `usage.py` | Platform `token_usage_daily` | Parquet | YYYY-MM-DD 이후 |
| `audit.py` | Platform `file_audit_logs` | JSONL (PII masked) | N일 이내 |

## 로컬 실행

```bash
export DATABASE_URL="postgresql://bedrock_admin:***@aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com:5432/bedrock_platform"

python -m ops.export.chats --user TESTUSER01 --since 90 --output chats.jsonl
python -m ops.export.skills --status approved --output skills.csv
python -m ops.export.usage --since 2026-04-01 --output usage.parquet
python -m ops.export.audit --since-days 30 --output audit.jsonl
```

## Container 실행

### Build + Push

```bash
ECR="680877507363.dkr.ecr.ap-northeast-2.amazonaws.com"

docker build --platform linux/amd64 \
  -t "$ECR/bedrock-claude/ops-export:latest" \
  -f ops/export/Dockerfile .

aws ecr get-login-password --region ap-northeast-2 | \
  docker login --username AWS --password-stdin "$ECR"

docker push "$ECR/bedrock-claude/ops-export:latest"
```

### EKS Job 실행 (수동)

```bash
ECR="680877507363.dkr.ecr.ap-northeast-2.amazonaws.com"

kubectl run ops-export-chats --rm -i --restart=Never \
  --namespace=platform \
  --image="$ECR/bedrock-claude/ops-export:latest" \
  --env="DATABASE_URL=postgresql://..." \
  --command -- python -m ops.export.chats --user TESTUSER01 --since 90
```

### K8s CronJob (Phase 1c 목표)

Phase 1c에서 일 1회 전체 usage + audit을 S3 Vault에 자동 저장 CronJob 도입 예정.

## IAM / RBAC

- **필요 권한**: Platform RDS `SELECT` on 대상 테이블 (`chat`, `skills`, `token_usage_daily`, `file_audit_logs`)
- **금지 권한**: `INSERT/UPDATE/DELETE` — 별도 read-only DB user 권장
- **이미지 user**: uid=1000 non-root (최소권한)

## PII 마스킹

`audit.py` 는 `user_email` 필드 자동 마스킹:

- `alice@skons.net` → `a***@skons.net`
- `010-1234-5678` → `010-****-****`
- `None` → `None` passthrough

구현: `ops/export/_common.py:mask_pii`.
정규식 한계:
- 이메일: 단순 `local@domain` 매칭. RFC 5321 100% 준수 아님 (SK 내부 주소엔 충분).
- 전화번호: 한국 포맷(`DDD-DDD(D)-DDDD`) 만 매칭. 국제번호는 passthrough.

## 데이터 범위 준수

각 스크립트는 **메타데이터만** export — 실제 본문/내용 제외:
- `chats.py` — chat 제목 + message count만 (JSONB 본문 제외)
- `skills.py` — id/name/status/author (code/parameters 제외)
- `usage.py` — 집계 토큰 수 + 비용 (prompt 본문 제외)
- `audit.py` — file_path + action (file_content 제외)

## 테스트

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/feat-phase1a-security-hardening
source .venv-export/bin/activate
PYTHONPATH=. pytest tests/unit/ -v
```

Phase 1a 기준 총 15 tests:
- `test_export_common.py` (mask_pii + resolve_username + db_session)
- `test_export_chats.py` (JSONL format + empty + kwargs)
- `test_export_skills.py` (CSV filter + header)
- `test_export_usage.py` (Parquet schema + empty)
- `test_export_audit.py` (PII masking + since_days + None passthrough)

## Phase 1a scope 밖 (Phase 1c backlog)

- S3 Vault 직접 upload (현재 로컬 파일 출력)
- 이메일 외 추가 PII 필드(주소, 생년월일 등) 마스킹
- 사용자 셀프 서비스 UI (Admin Dashboard 확장)
- K8s CronJob 자동화 + 실행 감사 로그
- 국제 전화번호 포맷 지원

## 관련 결정 문서

- `docs/superpowers/specs/2026-04-12-phase1a-security-hardening-design.md`
- `docs/decisions/phase1a-samesite-strict-vs-lax.md`
