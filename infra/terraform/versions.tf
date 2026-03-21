# =============================================================================
# Terraform & Provider 버전 설정
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # 원격 상태 저장소 (선택사항 - S3 백엔드)
  # 팀 작업 시 활성화. 혼자 작업 시에는 로컬 상태로 충분.
  # backend "s3" {
  #   bucket = "your-terraform-state-bucket"
  #   key    = "bedrock-ai-agent/terraform.tfstate"
  #   region = "ap-northeast-2"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "bedrock-ai-agent"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
