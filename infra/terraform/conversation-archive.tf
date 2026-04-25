# =============================================================================
# Conversation Archive: 사용자 대화 JSONL S3 영구 보존
#
# EFS 내 .claude-backup/projects/**/*.jsonl 을 일별 증분 sync.
# DuckDB / Athena로 사용 패턴·AI 학습 데이터·보안 감사 분석 가능.
#
# 연관 리소스:
#   - K8s CronJob: infra/k8s/platform/conversation-archiver.yaml
#   - EFS PVC: efs-audit-reader-pvc (platform ns, subPath 없이 전체 마운트)
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
