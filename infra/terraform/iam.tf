# =============================================================================
# IAM Roles for Bedrock Access (IRSA)
#
# IRSA = IAM Roles for Service Accounts
# K8s ServiceAccount에 IAM Role을 연결하는 AWS EKS 기능.
#
# 흐름:
#   K8s ServiceAccount (claude-terminal-sa)
#     ↓ 연결
#   IAM Role (bedrock-access-role)
#     ↓ 권한
#   Bedrock InvokeModel API
#
# 이렇게 하면 Pod이 별도의 AWS 키 없이도 Bedrock API를 호출할 수 있음.
# Pod 시작 시 AWS SDK가 자동으로 임시 자격증명을 주입받음.
# =============================================================================

# ----- Bedrock 접근용 IAM Role -----

resource "aws_iam_role" "bedrock_access" {
  name = "${var.project_name}-bedrock-access"

  # IRSA 신뢰 정책: EKS의 OIDC Provider를 통해
  # 특정 ServiceAccount만 이 역할을 사용할 수 있도록 제한
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
          "${replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:claude-sessions:claude-terminal-sa"
          "${replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
}

# ----- Bedrock InvokeModel 권한 -----
# Claude 모델 호출에 필요한 최소 권한만 부여

resource "aws_iam_role_policy" "bedrock_invoke" {
  name = "${var.project_name}-bedrock-invoke"
  role = aws_iam_role.bedrock_access.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowBedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          # Foundation models (직접 호출)
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
          # Cross-region inference profiles (account ID 포함 필수)
          "arn:aws:bedrock:*:680877507363:inference-profile/us.anthropic.claude-*",
          "arn:aws:bedrock:*:680877507363:inference-profile/global.anthropic.claude-*",
          # Global inference profiles
          "arn:aws:bedrock:*::inference-profile/us.anthropic.claude-*",
          "arn:aws:bedrock:*::inference-profile/global.anthropic.claude-*"
        ]
      },
      {
        Sid    = "AllowModelDiscovery"
        Effect = "Allow"
        Action = [
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModel"
        ]
        Resource = "*"
      },
      # ----- TANGO 알람 데이터 접근 -----
      # S3: tango-alarm-logs 버킷 읽기 (1년 아카이브 데이터)
      {
        Sid    = "AllowTangoS3Read"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = [
          "arn:aws:s3:::tango-alarm-logs",
          "arn:aws:s3:::tango-alarm-logs/*"
        ]
      },
      # Athena: 아카이브 데이터 SQL 쿼리
      {
        Sid    = "AllowTangoAthenaQuery"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
          "athena:GetWorkGroup"
        ]
        Resource = [
          "arn:aws:athena:ap-northeast-2:680877507363:workgroup/primary"
        ]
      },
      # Glue: Athena가 테이블 메타데이터 조회에 필요
      {
        Sid    = "AllowTangoGlueCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetPartitions"
        ]
        Resource = [
          "arn:aws:glue:ap-northeast-2:680877507363:catalog",
          "arn:aws:glue:ap-northeast-2:680877507363:database/tango_logs",
          "arn:aws:glue:ap-northeast-2:680877507363:table/tango_logs/*"
        ]
      },
      # S3: Athena 쿼리 결과 저장 (같은 버킷의 athena-results/ 프리픽스)
      {
        Sid    = "AllowAthenaResultsWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:AbortMultipartUpload"
        ]
        Resource = [
          "arn:aws:s3:::tango-alarm-logs/athena-results/*"
        ]
      }
    ]
  })
}

# =============================================================================
# IAM Role: Bedrock Access Gateway IRSA (openwebui 네임스페이스)
#
# Bedrock Access Gateway(BAG)가 사용하는 IRSA.
# BAG는 OpenAI 호환 API 요청을 AWS Bedrock으로 프록시하므로
# InvokeModel 권한이 필요.
#
# 연결 대상 ServiceAccount: openwebui/bedrock-ag-sa
# (claude-sessions:claude-terminal-sa 와 별개 — 네임스페이스 격리)
#
# 주의: TANGO S3/Athena 권한은 부여하지 않음
#   BAG는 LLM 호출 프록시 역할만, 데이터 접근은 Console Pod 전용
# =============================================================================

resource "aws_iam_role" "bedrock_ag_access" {
  name = "${var.project_name}-bedrock-ag-access"

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
          # openwebui 네임스페이스의 bedrock-ag-sa ServiceAccount 전용
          "${replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:openwebui:bedrock-ag-sa"
          "${replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = {
    Name    = "${var.project_name}-bedrock-ag-access"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }
}

resource "aws_iam_role_policy" "bedrock_ag_invoke" {
  name = "${var.project_name}-bedrock-ag-invoke"
  role = aws_iam_role.bedrock_ag_access.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowBedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
          "arn:aws:bedrock:*:680877507363:inference-profile/us.anthropic.claude-*",
          "arn:aws:bedrock:*:680877507363:inference-profile/global.anthropic.claude-*",
          "arn:aws:bedrock:*::inference-profile/us.anthropic.claude-*",
          "arn:aws:bedrock:*::inference-profile/global.anthropic.claude-*"
        ]
      },
      {
        Sid    = "AllowModelDiscovery"
        Effect = "Allow"
        Action = [
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModel"
        ]
        Resource = "*"
      }
    ]
  })
}

output "bedrock_ag_role_arn" {
  description = "Bedrock AG IRSA Role ARN (K8s ServiceAccount openwebui/bedrock-ag-sa에 연결)"
  value       = aws_iam_role.bedrock_ag_access.arn
}

# =============================================================================
# IAM Role: Auth Gateway Bedrock IRSA (T20 선행 조건)
#
# auth-gateway의 bedrock_proxy.py가 Bedrock을 직접 호출하기 위한 IRSA.
# Console Pod(T20)이 auth-gateway를 통해 Claude에 접근하는 경로:
#   Console Pod → auth-gateway /v1/messages → Bedrock
#
# 연결 대상 ServiceAccount: platform/platform-admin-sa
# (auth-gateway Deployment에서 사용 중인 SA)
#
# 주의: node role에 Bedrock 권한 미부여 → IRSA 없으면 502 AccessDenied
#
# 이력(참고): Phase 0(2026-04-12) 시점에는 bedrock-claude-platform-admin
# (AWS CLI 관리)을 가리켜 drift가 있었으며, Phase 1a(2026-04-13)에서
# terraform import + manifest annotation 교체로 Option A 정리 완료.
# 현 상태: platform-admin-sa → 이 role(terraform 관리) 일원화.
# =============================================================================

resource "aws_iam_role" "auth_gateway_bedrock" {
  name = "${var.project_name}-auth-gateway-bedrock"

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
          # platform 네임스페이스의 platform-admin-sa ServiceAccount 전용
          "${replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:platform:platform-admin-sa"
          "${replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = {
    Name    = "${var.project_name}-auth-gateway-bedrock"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }
}

resource "aws_iam_role_policy" "auth_gateway_bedrock_invoke" {
  name = "${var.project_name}-auth-gateway-bedrock-invoke"
  role = aws_iam_role.auth_gateway_bedrock.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # auth-gateway AI adapter: OpenAI-compat /api/v1/ai/chat/completions 엔드포인트가
        # boto3 bedrock-runtime.converse_stream() 을 호출하므로 두 계열 액션 모두 필요.
        #
        # 액션 계열 설명:
        #   InvokeModel / InvokeModelWithResponseStream — 구형(직접 호출) API
        #   Converse / ConverseStream                  — 신형 대화 API (boto3 converse_stream)
        #
        # AWS는 Converse API를 별도 IAM 액션으로 분리했으므로
        # InvokeModelWithResponseStream 만으로는 converse_stream() 호출 시 AccessDenied 발생 가능.
        Sid    = "AllowBedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:Converse",
          "bedrock:ConverseStream"
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
          "arn:aws:bedrock:*:680877507363:inference-profile/us.anthropic.claude-*",
          "arn:aws:bedrock:*:680877507363:inference-profile/global.anthropic.claude-*",
          "arn:aws:bedrock:*::inference-profile/us.anthropic.claude-*",
          "arn:aws:bedrock:*::inference-profile/global.anthropic.claude-*"
        ]
      },
      {
        Sid    = "AllowModelDiscovery"
        Effect = "Allow"
        Action = [
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModel"
        ]
        Resource = "*"
      },
      {
        # admin-dashboard /infra 페이지 + sessions.py _scale_up_nodegroup 용 권한.
        # 2026-04-13 세션에서 AWS CLI로 우선 put-role-policy 적용 후 tf에도 반영.
        Sid    = "EKSNodegroupManagement"
        Effect = "Allow"
        Action = [
          "eks:ListNodegroups",
          "eks:DescribeNodegroup",
          "eks:UpdateNodegroupConfig",
          "eks:DescribeCluster"
        ]
        Resource = [
          "arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks",
          "arn:aws:eks:ap-northeast-2:680877507363:nodegroup/bedrock-claude-eks/*/*"
        ]
      }
    ]
  })
}

output "auth_gateway_bedrock_role_arn" {
  description = "Auth Gateway Bedrock IRSA Role ARN (platform/platform-admin-sa 연결)"
  value       = aws_iam_role.auth_gateway_bedrock.arn
}
