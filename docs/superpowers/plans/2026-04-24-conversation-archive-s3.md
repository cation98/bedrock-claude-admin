# Conversation Archive to S3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EFS에 저장된 사용자 대화 JSONL 파일을 K8s CronJob으로 매일 S3에 증분 sync하여 영구 보존하고 DuckDB/Athena로 자유 분석이 가능하게 한다.

**Architecture:** Terraform으로 S3 버킷과 IRSA IAM 역할을 생성하고, K8s CronJob이 기존 `efs-audit-reader-pvc`를 전체 마운트하여 `aws s3 sync`로 모든 사용자의 `.claude-backup/projects/` JSONL 파일을 S3에 올린다. 증분 sync이므로 이미 업로드된 파일은 skip된다.

**Tech Stack:** Terraform (AWS provider), K8s CronJob, amazon/aws-cli 이미지, AWS S3, IRSA

---

## File Map

| 파일 | 역할 |
|------|------|
| `infra/terraform/conversation-archive.tf` (신규) | S3 버킷 + IAM Role/Policy |
| `infra/k8s/platform/conversation-archiver.yaml` (신규) | ServiceAccount + CronJob |

---

## Task 1: Terraform — S3 버킷

**Files:**
- Create: `infra/terraform/conversation-archive.tf`

- [ ] **Step 1: 파일 생성**

```hcl
# infra/terraform/conversation-archive.tf
# =============================================================================
# Conversation Archive: 사용자 대화 JSONL S3 영구 보존
#
# EFS 내 .claude-backup/projects/**/*.jsonl 을 일별 증분 sync.
# DuckDB / Athena로 사용 패턴·AI 학습 데이터·보안 감사 분석 가능.
# =============================================================================

# ---- S3 버킷 ----

resource "aws_s3_bucket" "conversation_archive" {
  bucket = "bedrock-conversation-archives-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name    = "bedrock-conversation-archives"
    Owner   = "N1102359"
    Env     = "prod"
    Service = "bedrock-ai-agent"
  }
}

resource "aws_s3_bucket_versioning" "conversation_archive" {
  bucket = aws_s3_bucket.conversation_archive.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "conversation_archive" {
  bucket = aws_s3_bucket.conversation_archive.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "conversation_archive" {
  bucket                  = aws_s3_bucket.conversation_archive.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "conversation_archive" {
  bucket = aws_s3_bucket.conversation_archive.id

  rule {
    id     = "intelligent-tiering"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "INTELLIGENT_TIERING"
    }

    filter {}
  }
}
```

- [ ] **Step 2: plan 확인**

```bash
cd infra/terraform
terraform plan -target=aws_s3_bucket.conversation_archive \
               -target=aws_s3_bucket_versioning.conversation_archive \
               -target=aws_s3_bucket_server_side_encryption_configuration.conversation_archive \
               -target=aws_s3_bucket_public_access_block.conversation_archive \
               -target=aws_s3_bucket_lifecycle_configuration.conversation_archive
```

Expected: `Plan: 5 to add, 0 to change, 0 to destroy.`

- [ ] **Step 3: apply**

```bash
terraform apply -target=aws_s3_bucket.conversation_archive \
                -target=aws_s3_bucket_versioning.conversation_archive \
                -target=aws_s3_bucket_server_side_encryption_configuration.conversation_archive \
                -target=aws_s3_bucket_public_access_block.conversation_archive \
                -target=aws_s3_bucket_lifecycle_configuration.conversation_archive \
                -auto-approve
```

Expected: `Apply complete! Resources: 5 added, 0 changed, 0 destroyed.`

- [ ] **Step 4: 버킷 존재 확인**

```bash
aws s3 ls | grep bedrock-conversation-archives
```

Expected: `bedrock-conversation-archives-680877507363` 출력

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/conversation-archive.tf
git commit -m "feat(infra): add S3 bucket for conversation archive"
```

---

## Task 2: Terraform — IAM Role (IRSA)

**Files:**
- Modify: `infra/terraform/conversation-archive.tf` (IAM 리소스 추가)

- [ ] **Step 1: IAM Role + Policy 블록 파일 끝에 추가**

```hcl
# ---- IAM Role (IRSA) for CronJob ----

resource "aws_iam_role" "conversation_archiver" {
  name = "${var.project_name}-conversation-archiver"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.eks.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:platform:conversation-archiver-sa"
          "${replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = {
    Name    = "${var.project_name}-conversation-archiver"
    Owner   = "N1102359"
    Env     = "prod"
    Service = "bedrock-ai-agent"
  }
}

resource "aws_iam_policy" "conversation_archiver_s3" {
  name        = "${var.project_name}-conversation-archiver-s3"
  description = "S3 write access for conversation archive CronJob"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket"
      ]
      Resource = [
        aws_s3_bucket.conversation_archive.arn,
        "${aws_s3_bucket.conversation_archive.arn}/*"
      ]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "conversation_archiver_s3" {
  role       = aws_iam_role.conversation_archiver.name
  policy_arn = aws_iam_policy.conversation_archiver_s3.arn
}

output "conversation_archiver_role_arn" {
  value = aws_iam_role.conversation_archiver.arn
}
```

- [ ] **Step 2: plan 확인**

```bash
terraform plan -target=aws_iam_role.conversation_archiver \
               -target=aws_iam_policy.conversation_archiver_s3 \
               -target=aws_iam_role_policy_attachment.conversation_archiver_s3
```

Expected: `Plan: 3 to add, 0 to change, 0 to destroy.`

- [ ] **Step 3: apply**

```bash
terraform apply -target=aws_iam_role.conversation_archiver \
                -target=aws_iam_policy.conversation_archiver_s3 \
                -target=aws_iam_role_policy_attachment.conversation_archiver_s3 \
                -auto-approve
```

Expected: `Apply complete! Resources: 3 added, 0 changed, 0 destroyed.`

- [ ] **Step 4: Role ARN 확인**

```bash
terraform output conversation_archiver_role_arn
```

Expected: `"arn:aws:iam::680877507363:role/bedrock-claude-conversation-archiver"`

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/conversation-archive.tf
git commit -m "feat(infra): add IRSA role for conversation-archiver CronJob"
```

---

## Task 3: K8s — ServiceAccount + CronJob

**Files:**
- Create: `infra/k8s/platform/conversation-archiver.yaml`

- [ ] **Step 1: manifest 파일 생성**

```yaml
# infra/k8s/platform/conversation-archiver.yaml
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: conversation-archiver-sa
  namespace: platform
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::680877507363:role/bedrock-claude-conversation-archiver
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: conversation-archiver
  namespace: platform
spec:
  schedule: "0 17 * * *"   # 매일 02:00 KST (UTC 17:00)
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: conversation-archiver-sa
          restartPolicy: OnFailure
          containers:
            - name: archiver
              image: amazon/aws-cli:latest
              resources:
                requests:
                  cpu: "100m"
                  memory: "128Mi"
                limits:
                  cpu: "500m"
                  memory: "512Mi"
              env:
                - name: AWS_REGION
                  value: ap-northeast-2
              command:
                - /bin/sh
                - -c
                - |
                  set -e
                  BUCKET="bedrock-conversation-archives-680877507363"
                  EFS_USERS="/efs/users"

                  echo "[$(date -u)] Archive start"

                  for user_dir in "$EFS_USERS"/*/; do
                    [ -d "$user_dir" ] || continue
                    username=$(basename "$user_dir")
                    backup_dir="$user_dir/.claude-backup"

                    if [ -d "$backup_dir/projects" ]; then
                      aws s3 sync \
                        "$backup_dir/projects/" \
                        "s3://$BUCKET/conversations/$username/projects/" \
                        --storage-class INTELLIGENT_TIERING \
                        --no-progress \
                        --region ap-northeast-2
                    fi

                    if [ -f "$backup_dir/history.jsonl" ]; then
                      aws s3 cp \
                        "$backup_dir/history.jsonl" \
                        "s3://$BUCKET/conversations/$username/history.jsonl" \
                        --storage-class INTELLIGENT_TIERING \
                        --no-progress \
                        --region ap-northeast-2 2>/dev/null || true
                    fi
                  done

                  echo "[$(date -u)] Archive complete"
              volumeMounts:
                - name: efs-users
                  mountPath: /efs
                  readOnly: true
          volumes:
            - name: efs-users
              persistentVolumeClaim:
                claimName: efs-audit-reader-pvc
                readOnly: true
```

- [ ] **Step 2: dry-run으로 manifest 검증**

```bash
kubectl apply -f infra/k8s/platform/conversation-archiver.yaml --dry-run=client
```

Expected: `serviceaccount/conversation-archiver-sa configured (dry run)` + `cronjob.batch/conversation-archiver configured (dry run)`

- [ ] **Step 3: apply**

```bash
kubectl apply -f infra/k8s/platform/conversation-archiver.yaml
```

Expected:
```
serviceaccount/conversation-archiver-sa created
cronjob.batch/conversation-archiver created
```

- [ ] **Step 4: CronJob 상태 확인**

```bash
kubectl get cronjob conversation-archiver -n platform
```

Expected: `SCHEDULE: 0 17 * * *`, `SUSPEND: False`, `ACTIVE: 0`

- [ ] **Step 5: Commit**

```bash
git add infra/k8s/platform/conversation-archiver.yaml
git commit -m "feat(k8s): add conversation-archiver CronJob for S3 JSONL archive"
```

---

## Task 4: 통합 검증 — 수동 Job 실행

- [ ] **Step 1: 수동 Job 트리거**

```bash
kubectl create job conversation-archiver-manual \
  --from=cronjob/conversation-archiver \
  -n platform
```

- [ ] **Step 2: Job 완료 대기**

```bash
kubectl wait job/conversation-archiver-manual \
  -n platform \
  --for=condition=complete \
  --timeout=300s
```

Expected: `job.batch/conversation-archiver-manual condition met`

- [ ] **Step 3: 로그 확인**

```bash
kubectl logs -n platform \
  -l job-name=conversation-archiver-manual \
  --tail=50
```

Expected:
```
[<timestamp>] Archive start
[<timestamp>] Archive complete
```

오류 없이 두 라인이 출력되면 정상. `upload:` 라인이 있으면 실제 파일 업로드된 것.

- [ ] **Step 4: S3 업로드 결과 확인**

```bash
aws s3 ls s3://bedrock-conversation-archives-680877507363/conversations/ \
  --recursive \
  --human-readable \
  | head -20
```

Expected: 사용자명별 JSONL 파일 목록 출력

- [ ] **Step 5: 샘플 파일 다운로드해서 내용 확인**

```bash
# 첫 번째 파일 경로 추출
SAMPLE=$(aws s3 ls s3://bedrock-conversation-archives-680877507363/conversations/ \
  --recursive | grep ".jsonl" | head -1 | awk '{print $4}')

aws s3 cp "s3://bedrock-conversation-archives-680877507363/$SAMPLE" /tmp/sample.jsonl
head -3 /tmp/sample.jsonl
rm /tmp/sample.jsonl
```

Expected: Claude Code 대화 JSON 라인 출력 (role, content 등 필드 포함)

- [ ] **Step 6: 수동 Job 정리**

```bash
kubectl delete job conversation-archiver-manual -n platform
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: conversation archive to S3 — complete

- Terraform: S3 bucket + IRSA IAM role
- K8s CronJob: daily 02:00 KST EFS→S3 JSONL sync
- Verified: manual job run succeeded"
```

---

## 검증 완료 기준

| 항목 | 확인 방법 |
|------|----------|
| S3 버킷 생성 | `aws s3 ls \| grep bedrock-conversation-archives` |
| IAM Role 생성 | `terraform output conversation_archiver_role_arn` |
| CronJob 등록 | `kubectl get cronjob -n platform` |
| 수동 실행 성공 | Job condition=complete + 로그 "Archive complete" |
| S3 파일 존재 | `aws s3 ls s3://bedrock-conversation-archives-.../conversations/ --recursive` |
| JSONL 내용 유효 | head 출력에 JSON role/content 필드 |

---

## 분석 사용법 (구현 후)

```python
import duckdb

conn = duckdb.connect()
conn.execute("INSTALL httpfs; LOAD httpfs;")
conn.execute("SET s3_region='ap-northeast-2';")

# 전 사용자 대화 role별 집계
conn.execute("""
  SELECT role, COUNT(*) as cnt
  FROM read_json_auto(
    's3://bedrock-conversation-archives-680877507363/conversations/**/*.jsonl'
  )
  GROUP BY role
""").df()
```
