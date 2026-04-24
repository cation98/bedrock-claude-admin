# Conversation Archive to S3 — Design Spec

**Date**: 2026-04-24  
**Status**: Approved  
**Author**: cation98

---

## 배경 및 목적

사용자별 Claude Code 대화 이력은 EFS 내 JSONL 파일로 저장된다. 현재 `storage_retention=180d` 정책에 따라 만료 시 삭제 예정이나, 이 원본 데이터를 영구 보존하고 다양한 분석(사용 패턴, AI 학습 데이터, 보안 감사, 자유 탐색)에 활용하기 위해 S3 아카이브 파이프라인을 구축한다.

---

## 아키텍처

```
EFS
  users/{username}/.claude-backup/
    projects/{project_hash}/{session_id}.jsonl   ← 대화 JSONL
    history.jsonl                                 ← 전체 사용 이력
         │
         │ K8s CronJob (매일 02:00 KST)
         │ aws s3 sync (증분, 이미 업로드된 파일 skip)
         ▼
S3: bedrock-conversation-archives
  conversations/
    {username}/
      projects/{project_hash}/{session_id}.jsonl
      history.jsonl
```

**핵심 원칙**
- 원본 JSONL 포맷 그대로 보존 (AI 학습, 감사, 자유 분석 모두 대응)
- 증분 sync — 이미 올라간 파일은 재전송하지 않음
- auth-gateway 변경 없음 — 별도 CronJob이 단일 책임

---

## 컴포넌트

### 1. K8s CronJob: `conversation-archiver`

| 항목 | 값 |
|------|-----|
| 네임스페이스 | `platform` |
| 이미지 | `amazon/aws-cli:latest` |
| 스케줄 | `0 17 * * *` (02:00 KST) |
| restartPolicy | `OnFailure` |
| successfulJobsHistoryLimit | 3 |
| failedJobsHistoryLimit | 3 |
| EFS 마운트 | `efs-audit-reader-pvc` (platform ns) — subPath 없이 전체 마운트 → `/efs` |
| IAM | IRSA ServiceAccount `conversation-archiver-sa` |
| resources | requests: cpu 100m / mem 128Mi, limits: cpu 500m / mem 512Mi |

**실행 스크립트 (CronJob command)**
```bash
#!/bin/sh
set -e

BUCKET="bedrock-conversation-archives"
EFS_USERS="/efs/users"

for user_dir in "$EFS_USERS"/*/; do
  [ -d "$user_dir" ] || continue
  username=$(basename "$user_dir")
  backup_dir="$user_dir/.claude-backup"

  # projects/ 대화 JSONL 증분 sync
  if [ -d "$backup_dir/projects" ]; then
    aws s3 sync \
      "$backup_dir/projects/" \
      "s3://$BUCKET/conversations/$username/projects/" \
      --storage-class INTELLIGENT_TIERING \
      --no-progress
  fi

  # history.jsonl
  if [ -f "$backup_dir/history.jsonl" ]; then
    aws s3 cp \
      "$backup_dir/history.jsonl" \
      "s3://$BUCKET/conversations/$username/history.jsonl" \
      --storage-class INTELLIGENT_TIERING \
      --no-progress 2>/dev/null || true
  fi
done

echo "Archive complete: $(date -u)"
```

### 2. IRSA ServiceAccount

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: conversation-archiver-sa
  namespace: platform
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::680877507363:role/conversation-archiver-role
```

**IAM Role 정책**
```json
{
  "Effect": "Allow",
  "Action": [
    "s3:PutObject",
    "s3:GetObject",
    "s3:ListBucket"
  ],
  "Resource": [
    "arn:aws:s3:::bedrock-conversation-archives",
    "arn:aws:s3:::bedrock-conversation-archives/*"
  ]
}
```

### 3. S3 버킷: `bedrock-conversation-archives`

| 항목 | 값 |
|------|-----|
| 리전 | `ap-northeast-2` |
| 버전 관리 | 활성화 |
| 암호화 | SSE-S3 |
| 퍼블릭 접근 | 전체 차단 |
| Lifecycle | 90일 후 INTELLIGENT_TIERING 전환 |
| 태그 | `Owner=N1102359`, `Env=prod`, `Service=bedrock-ai-agent` |

---

## 쿼리 방법

### DuckDB (로컬 Python — 권장 시작점)

```python
import duckdb

conn = duckdb.connect()
conn.execute("INSTALL httpfs; LOAD httpfs;")
conn.execute("SET s3_region='ap-northeast-2';")

# 전 사용자 메시지 role별 집계
conn.execute("""
  SELECT role, COUNT(*) as cnt
  FROM read_json_auto(
    's3://bedrock-conversation-archives/conversations/**/*.jsonl'
  )
  GROUP BY role
""").df()

# 특정 사용자 대화 조회
conn.execute("""
  SELECT *
  FROM read_json_auto(
    's3://bedrock-conversation-archives/conversations/n1102359/projects/**/*.jsonl'
  )
""").df()
```

### Athena (대용량 SQL)

```sql
CREATE EXTERNAL TABLE conversations (
  role       STRING,
  content    STRING,
  timestamp  STRING,
  session_id STRING,
  usage      STRUCT<input_tokens:INT, output_tokens:INT>
)
PARTITIONED BY (username STRING)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://bedrock-conversation-archives/conversations/';
```

### 분석 목적별 적합 도구

| 목적 | 도구 |
|------|------|
| 사용 패턴 집계 | Athena 또는 DuckDB |
| AI 학습 데이터 추출 | DuckDB → Python → 파일 export |
| 보안 감사 (특정 발화 검색) | Athena `LIKE` / DuckDB `WHERE content LIKE` |
| 자유 탐색 | DuckDB + Jupyter |

---

## 구현 범위

### 포함
- Terraform: S3 버킷 + 버킷 정책 + Lifecycle 규칙
- Terraform: IAM Role (IRSA) + 정책
- K8s: ServiceAccount + CronJob manifest (`infra/k8s/`)

### 재사용 (신규 생성 불필요)
- EFS PVC: `efs-audit-reader-pvc` (platform ns, `fs-0a2b5924041425002` 전체 마운트, RWX)
- AWS Account: `680877507363`

### 제외
- Athena 테이블 생성 (데이터 쌓인 후 필요 시 별도 작업)
- 기존 EFS JSONL의 소급 적용 여부 (첫 실행 시 전체 sync로 자동 처리)
- auth-gateway `storage_cleanup_loop` 연동 (별도 CronJob으로 분리 유지)

---

## 에러 처리

- `set -e` — 스크립트 오류 시 Job 실패 처리 → K8s `OnFailure` 재시도
- `history.jsonl` 복사 실패는 `|| true` — 파일 없는 사용자 무시
- CronJob 실패 시 `failedJobsHistoryLimit=3` 보존 → kubectl logs로 진단

---

## 보안

- CronJob Pod은 EFS read-only 마운트 (파일 수정 불가)
- S3 버킷 퍼블릭 접근 전체 차단
- IRSA로 최소 권한 — 해당 버킷 외 S3 접근 불가
- Pod에 AWS credential 하드코딩 없음 (IRSA 토큰만 사용)
