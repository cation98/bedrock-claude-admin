# =============================================================================
# 입력 변수 정의
# 실제 값은 terraform.tfvars 파일에서 설정
# =============================================================================

variable "aws_region" {
  description = "AWS 리전 (서울)"
  type        = string
  default     = "ap-northeast-2"
}

variable "environment" {
  description = "환경 이름 (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "프로젝트 이름 (리소스 네이밍에 사용)"
  type        = string
  default     = "bedrock-claude"
}

# ----- VPC -----

variable "vpc_cidr" {
  description = "VPC CIDR 블록"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "사용할 가용영역 목록 (EKS는 최소 2개 필요)"
  type        = list(string)
  default     = ["ap-northeast-2a", "ap-northeast-2c"]
}

# ----- EKS -----

variable "eks_cluster_version" {
  description = "EKS Kubernetes 버전"
  type        = string
  default     = "1.31"
}

variable "eks_node_instance_types" {
  description = "EKS 워커 노드 인스턴스 타입"
  type        = list(string)
  default     = ["m5.large"]
}

variable "eks_node_desired_size" {
  description = "워커 노드 희망 개수"
  type        = number
  default     = 2
}

variable "eks_node_min_size" {
  description = "워커 노드 최소 개수"
  type        = number
  default     = 2
}

variable "eks_node_max_size" {
  description = "워커 노드 최대 개수 (Phase 2: 50명 실습 시 확장)"
  type        = number
  default     = 4
}

# ----- Bedrock -----

variable "bedrock_region" {
  description = "Bedrock Claude 모델이 있는 리전 (서울에는 아직 없을 수 있음)"
  type        = string
  default     = "us-east-1"
}
