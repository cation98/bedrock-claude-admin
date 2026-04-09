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

# ----- 기존 SKO VPC (data source 참조) -----

variable "vpc_id" {
  description = "기존 SKO VPC ID (절대 수정/삭제하지 않음)"
  type        = string
  default     = "vpc-075deed66fcc7f348"
}

variable "eks_subnet_ids" {
  description = "EKS Control Plane이 사용할 기존 서브넷 (최소 2개 AZ)"
  type        = list(string)
  default = [
    "subnet-03f741587efae3ffb", # sko-public-subnet-a (ap-northeast-2a)
    "subnet-02a58f3358354e490", # sko-public-subnet-b (ap-northeast-2b)
    "subnet-083d39916aeea24cd", # sko-public-subnet-c (ap-northeast-2c)
  ]
}

variable "private_route_table_id" {
  description = "기존 private route table (NAT Instance + Bedrock Endpoint 라우팅 포함)"
  type        = string
  default     = "rtb-0700167f652e4360a" # sko-private-subnet-rt
}

# ----- EKS 전용 Private Subnets (신규 생성) -----

variable "eks_private_subnet_cidrs" {
  description = "EKS 워커 노드 전용 private 서브넷 CIDR (신규 생성)"
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.20.0/24"]
}

variable "eks_private_subnet_azs" {
  description = "EKS private 서브넷 가용영역"
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
  description = "워커 노드 최소 개수 (0 = 야간 완전 축소 가능)"
  type        = number
  default     = 0
}

variable "eks_node_max_size" {
  description = "워커 노드 최대 개수 (Phase 2: 50명 실습 시 확장)"
  type        = number
  default     = 4
}

# ----- 1:1 전용 노드그룹 (t3.medium, 사용자별 1 node) -----

variable "eks_dedicated_node_instance_types" {
  description = "1:1 전용 노드 인스턴스 타입"
  type        = list(string)
  default     = ["t3.medium"]
}

variable "eks_dedicated_node_desired_size" {
  description = "1:1 전용 노드 희망 개수"
  type        = number
  default     = 2
}

variable "eks_dedicated_node_min_size" {
  description = "1:1 전용 노드 최소 개수 (0 = 야간 완전 축소 가능)"
  type        = number
  default     = 0
}

variable "eks_dedicated_node_max_size" {
  description = "1:1 전용 노드 최대 개수"
  type        = number
  default     = 55
}

# ----- Bedrock -----

variable "bedrock_region" {
  description = "Bedrock Claude 모델이 있는 리전"
  type        = string
  default     = "us-east-1"
}
