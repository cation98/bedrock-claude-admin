# =============================================================================
# VPC — 기존 SKO VPC 참조 (data source only)
#
# ⚠️ 기존 리소스를 절대 생성/수정/삭제하지 않음
#
# 기존 인프라:
#   sko-vpc (10.0.0.0/16)  vpc-075deed66fcc7f348
#   ├── sko-public-subnet-a   10.0.0.0/24 (2a) → IGW 라우팅
#   ├── sko-private-subnet-b  10.0.1.0/24 (2b) → NAT Instance 라우팅
#   ├── sko-public-subnet-c   10.0.2.0/24 (2c) → IGW 라우팅
#   └── sko-public-subnet-b   10.0.4.0/24 (2b) → IGW 라우팅
#   + sko-nat-instance (i-07caaab6581d70009) → private subnet 아웃바운드
#   + Bedrock VPC Endpoint (vpce-0fab76c138da3a84a) → Bedrock API 내부 접근
#   + sko-private-subnet-rt → NAT Instance + Bedrock Endpoint 라우팅 완료
#
# 신규 생성:
#   + EKS 전용 private 서브넷 2개 (10.0.10.0/24, 10.0.20.0/24)
#   + 기존 sko-private-subnet-rt에 연결 (NAT Instance + Bedrock Endpoint 재활용)
# =============================================================================

# ----- 기존 VPC 참조 -----

data "aws_vpc" "sko" {
  id = var.vpc_id
}

# ----- 기존 Private Route Table 참조 -----
# NAT Instance + Bedrock VPC Endpoint 라우팅이 이미 설정되어 있음

data "aws_route_table" "private" {
  route_table_id = var.private_route_table_id
}

# ----- EKS 전용 Private Subnets (신규 생성) -----
# 기존 서브넷을 변경하지 않고, EKS 워커 노드 전용 private 서브넷을 새로 생성
# 기존 NAT Instance를 통해 아웃바운드 인터넷 접근
# 기존 Bedrock VPC Endpoint를 통해 Bedrock API 접근

resource "aws_subnet" "eks_private" {
  count = length(var.eks_private_subnet_cidrs)

  vpc_id            = data.aws_vpc.sko.id
  cidr_block        = var.eks_private_subnet_cidrs[count.index]
  availability_zone = var.eks_private_subnet_azs[count.index]

  tags = {
    Name                                               = "${var.project_name}-eks-private-${var.eks_private_subnet_azs[count.index]}"
    "kubernetes.io/role/internal-elb"                   = "1"
    "kubernetes.io/cluster/${var.project_name}-eks"     = "shared"
  }
}

# ----- 기존 Private Route Table에 신규 서브넷 연결 -----
# NAT Instance + Bedrock Endpoint 라우팅을 그대로 상속

resource "aws_route_table_association" "eks_private" {
  count          = length(aws_subnet.eks_private)
  subnet_id      = aws_subnet.eks_private[count.index].id
  route_table_id = data.aws_route_table.private.id
}
