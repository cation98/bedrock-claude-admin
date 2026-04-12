# =============================================================================
# ECR (Elastic Container Registry)
#
# Docker 이미지를 AWS에 저장하는 프라이빗 레지스트리.
# 빌드한 claude-code-terminal 이미지를 여기에 push하면
# EKS 워커 노드가 이미지를 pull하여 Pod을 실행.
# =============================================================================

resource "aws_ecr_repository" "claude_terminal" {
  name                 = "${var.project_name}/claude-code-terminal"
  image_tag_mutability = "MUTABLE" # latest 태그 덮어쓰기 허용

  # 이미지 스캔: push 시 자동 보안 취약점 검사
  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "${var.project_name}-claude-terminal"
  }
}

# 오래된 이미지 자동 삭제 정책 (비용 절감)
# 최근 10개 태그만 유지, 나머지 자동 삭제
resource "aws_ecr_lifecycle_policy" "claude_terminal" {
  repository = aws_ecr_repository.claude_terminal.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = {
        type = "expire"
      }
    }]
  })
}

# =============================================================================
# ECR: Bedrock Access Gateway (aws-samples/bedrock-access-gateway fork)
#
# aws-samples/bedrock-access-gateway 저장소를 내부 fork → 빌드 → 이 ECR에 push.
# OpenAI 호환 API를 AWS Bedrock으로 프록시하는 역할.
#
# 빌드 가이드:
#   git clone https://github.com/aws-samples/bedrock-access-gateway
#   cd bedrock-access-gateway
#   docker build -t bedrock-access-gateway .
#   docker tag bedrock-access-gateway:latest \
#     680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/${var.project_name}/bedrock-access-gateway:latest
#   aws ecr get-login-password | docker login --username AWS --password-stdin \
#     680877507363.dkr.ecr.ap-northeast-2.amazonaws.com
#   docker push 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/${var.project_name}/bedrock-access-gateway:latest
# =============================================================================

resource "aws_ecr_repository" "bedrock_ag" {
  name                 = "${var.project_name}/bedrock-access-gateway"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name    = "${var.project_name}-bedrock-ag"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }
}

resource "aws_ecr_lifecycle_policy" "bedrock_ag" {
  repository = aws_ecr_repository.bedrock_ag.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = {
        type = "expire"
      }
    }]
  })
}

output "bedrock_ag_ecr_url" {
  description = "Bedrock AG ECR URL (이미지 빌드 후 push 대상)"
  value       = aws_ecr_repository.bedrock_ag.repository_url
}
