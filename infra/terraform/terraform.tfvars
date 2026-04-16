# =============================================================================
# Terraform 변수 값 설정
# 이 파일을 terraform.tfvars로 복사하여 사용하세요.
# =============================================================================

aws_region  = "ap-northeast-2"
environment = "prod"

# ----- 기존 SKO VPC (참조만, 수정 안 함) -----
vpc_id = "vpc-075deed66fcc7f348"

eks_subnet_ids = [
  "subnet-03f741587efae3ffb", # sko-public-subnet-a (ap-northeast-2a)
  "subnet-02a58f3358354e490", # sko-public-subnet-b (ap-northeast-2b)
  "subnet-083d39916aeea24cd", # sko-public-subnet-c (ap-northeast-2c)
]

# 기존 private route table (NAT Instance + Bedrock VPC Endpoint 라우팅 포함)
private_route_table_id = "rtb-0700167f652e4360a"

# ----- EKS 전용 Private Subnets (신규 생성) -----
eks_private_subnet_cidrs = ["10.0.10.0/24", "10.0.20.0/24"]
eks_private_subnet_azs   = ["ap-northeast-2a", "ap-northeast-2c"]

# ----- EKS 노드 설정 -----
# main 노드그룹 (m5.large) — Phase 1b: 50명 상시 운용
# desired 6 / max 12 (Phase 1b: 50명 사용자 Pod 수용 + burst 허용)
# min 0 유지 (비운용 시간대 비용 하한)
eks_node_instance_types = ["m5.large"]
eks_node_desired_size   = 6
eks_node_min_size       = 0
eks_node_max_size       = 12

# ----- 1:1 전용 노드그룹 (t3.large) — 2026-04-17 medium→large 상향 -----
eks_dedicated_node_instance_types = ["t3.large"]
eks_dedicated_node_desired_size   = 2
eks_dedicated_node_min_size       = 0
eks_dedicated_node_max_size       = 55

# Bedrock 리전 (VPC Endpoint가 ap-northeast-2에서 Bedrock 접근 제공)
bedrock_region = "us-east-1"
