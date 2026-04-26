# =============================================================================
# S3 Vault: 민감 파일 격리 스토리지 + KMS 암호화
#
# 사용자 Pod에서 생성된 민감 파일(자격증명, 분석 결과 등)을 격리 저장.
# KMS Customer Managed Key(CMK)로 서버 측 암호화를 강제하여
# 암호화되지 않은 업로드를 차단하고, 90일 후 자동 삭제.
#
# 동작 방식:
#   1. KMS CMK 생성 → S3 버킷 SSE-KMS 기본 암호화 설정
#   2. 버킷 정책: 비암호화 PutObject 요청 거부
#   3. Public Access Block: 모든 공개 접근 차단
#   4. Lifecycle Rule: 90일 후 객체 자동 삭제 (비용 절감 + 보안)
#   5. 버전 관리: 활성화 (실수 삭제 복구 가능)
# =============================================================================

# ----- AWS Account ID (동적 참조) -----
# 버킷 이름에 account_id를 포함하여 전역 고유성 보장

data "aws_caller_identity" "current" {}

# ----- KMS Key: S3 Vault 전용 -----
# Customer Managed Key(CMK)로 암호화 키를 직접 관리
# AWS Managed Key 대비 장점: 키 정책 커스터마이징, 교체 주기 제어

resource "aws_kms_key" "s3_vault" {
  description             = "S3 Vault 버킷 SSE-KMS 암호화 키"
  deletion_window_in_days = 30   # Phase 1a 표준: ISMS-P 30일 유예 (실수 방지)
  enable_key_rotation     = true # 연 1회 자동 키 교체 (보안 모범 사례)
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootAccountFullAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowS3ServiceEncryption"
        Effect = "Allow"
        Principal = {
          Service = "s3.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt"
        ]
        Resource = "*"
      }
    ]
  })

  tags = {
    Name    = "${var.project_name}-s3-vault-key"
    Owner   = "N1102359"
    Env     = "prod"
    Service = "sko-claude-ai-agent"
  }
}

# KMS Key에 읽기 쉬운 별칭 부여
resource "aws_kms_alias" "s3_vault" {
  name          = "alias/${var.project_name}-s3-vault"
  target_key_id = aws_kms_key.s3_vault.key_id
}

# ----- S3 Bucket: Vault -----

resource "aws_s3_bucket" "vault" {
  bucket = "bedrock-claude-s3-vault-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name    = "${var.project_name}-s3-vault"
    Owner   = "N1102359"
    Env     = "prod"
    Service = "sko-claude-ai-agent"
  }
}

# ----- 버전 관리 -----
# 실수로 삭제한 객체를 복구할 수 있도록 버전 관리 활성화

resource "aws_s3_bucket_versioning" "vault" {
  bucket = aws_s3_bucket.vault.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ----- 기본 서버 측 암호화 (SSE-KMS) -----
# 모든 객체를 KMS CMK로 자동 암호화
# Bucket Key 활성화: KMS API 호출 횟수 ~99% 감소 → 비용 절감

resource "aws_s3_bucket_server_side_encryption_configuration" "vault" {
  bucket = aws_s3_bucket.vault.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3_vault.arn
    }
    bucket_key_enabled = true # KMS API 호출 비용 절감
  }
}

# ----- 공개 접근 완전 차단 -----
# 4가지 설정 모두 true → 어떤 경우에도 공개 접근 불가

resource "aws_s3_bucket_public_access_block" "vault" {
  bucket = aws_s3_bucket.vault.id

  block_public_acls       = true # Public ACL 설정 차단
  block_public_policy     = true # Public 버킷 정책 차단
  ignore_public_acls      = true # 기존 Public ACL 무시
  restrict_public_buckets = true # 크로스 계정 공개 접근 차단
}

# ----- Lifecycle Rule: 90일 후 자동 삭제 -----
# 민감 파일은 장기 보관 불필요 → 90일 후 현재 버전 삭제
# 삭제 마커의 이전 버전도 1일 후 정리 (저장 공간 절약)

resource "aws_s3_bucket_lifecycle_configuration" "vault" {
  bucket = aws_s3_bucket.vault.id

  # 의존성: 버전 관리가 먼저 활성화되어야 lifecycle이 정상 동작
  depends_on = [aws_s3_bucket_versioning.vault]

  rule {
    id     = "auto-delete-after-90-days"
    status = "Enabled"

    # 90일 후 현재 버전 만료 (삭제 마커로 전환)
    expiration {
      days = 90
    }

    # 이전 버전(삭제 마커 포함)도 1일 후 영구 삭제
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "drm-export-auto-expire"
    status = "Enabled"

    # object_type=drm_export 태그가 달린 임시 수출 객체: 1일 후 자동 만료.
    # Phase 3 TTL 데몬이 정상 동작 시 먼저 삭제하지만, 데몬 장애 시 백스톱 역할.
    filter {
      tag {
        key   = "object_type"
        value = "drm_export"
      }
    }

    expiration {
      days = 1
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

# ----- 오브젝트 소유권: BucketOwnerEnforced -----
# ACL을 완전히 비활성화하고 버킷 소유자가 모든 객체를 소유.
# 크로스 계정 PutObject 시 ACL grant 없이도 소유권 보장.

resource "aws_s3_bucket_ownership_controls" "vault" {
  bucket = aws_s3_bucket.vault.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# ----- 버킷 정책: 비암호화 업로드 거부 -----
# SSE-KMS 헤더 없는 PutObject 요청을 서버 측에서 차단
# 클라이언트가 암호화를 빼먹어도 업로드 자체가 실패

resource "aws_s3_bucket_policy" "vault" {
  bucket = aws_s3_bucket.vault.id

  # Public Access Block이 먼저 적용되어야 정책 충돌 방지
  depends_on = [aws_s3_bucket_public_access_block.vault]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyUnencryptedUploads"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.vault.arn}/*"
        Condition = {
          StringNotEquals = {
            "s3:x-amz-server-side-encryption" = "aws:kms"
          }
        }
      },
      {
        Sid       = "DenyWrongKMSKey"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.vault.arn}/*"
        Condition = {
          StringNotEqualsIfExists = {
            "s3:x-amz-server-side-encryption-aws-kms-key-id" = aws_kms_key.s3_vault.arn
          }
        }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.vault.arn,
          "${aws_s3_bucket.vault.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# ----- Outputs -----

output "s3_vault_bucket_name" {
  description = "S3 Vault 버킷 이름 (Pod에서 파일 업로드 시 사용)"
  value       = aws_s3_bucket.vault.id
}

output "s3_vault_bucket_arn" {
  description = "S3 Vault 버킷 ARN (IAM 정책에서 참조)"
  value       = aws_s3_bucket.vault.arn
}

output "s3_vault_kms_key_arn" {
  description = "S3 Vault KMS 키 ARN (암호화 키 참조)"
  value       = aws_kms_key.s3_vault.arn
}
