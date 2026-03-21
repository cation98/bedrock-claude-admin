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
          # Global inference profiles
          "arn:aws:bedrock:*::inference-profile/us.anthropic.claude-*"
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
