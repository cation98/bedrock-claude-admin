# =============================================================================
# Terraform Outputs
# terraform apply 완료 후 표시되는 값들
# 후속 작업(kubectl 설정, Docker push 등)에 필요한 정보
# =============================================================================

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "eks_cluster_name" {
  description = "EKS 클러스터 이름 (kubectl 설정에 사용)"
  value       = aws_eks_cluster.main.name
}

output "eks_cluster_endpoint" {
  description = "EKS API 서버 엔드포인트"
  value       = aws_eks_cluster.main.endpoint
}

output "ecr_repository_url" {
  description = "ECR 저장소 URL (docker push 대상)"
  value       = aws_ecr_repository.claude_terminal.repository_url
}

output "bedrock_role_arn" {
  description = "Bedrock 접근용 IAM Role ARN (K8s ServiceAccount에 연결)"
  value       = aws_iam_role.bedrock_access.arn
}

output "eks_oidc_provider_arn" {
  description = "EKS OIDC Provider ARN"
  value       = aws_iam_openid_connect_provider.eks.arn
}

# ----- 사용 가이드 -----

output "next_steps" {
  description = "다음 단계 안내"
  value       = <<-EOT

    =====================================================
    Infrastructure provisioned! Next steps:
    =====================================================

    1. kubectl 설정:
       aws eks update-kubeconfig --name ${aws_eks_cluster.main.name} --region ${var.aws_region}

    2. Docker 이미지 push:
       aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.claude_terminal.repository_url}
       docker tag claude-code-terminal:latest ${aws_ecr_repository.claude_terminal.repository_url}:latest
       docker push ${aws_ecr_repository.claude_terminal.repository_url}:latest

    3. K8s manifests 배포:
       kubectl apply -f ../k8s/

    =====================================================
  EOT
}
