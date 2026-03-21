# =============================================================================
# VPC — 기존 SKO VPC 참조 (data source)
#
# ⚠️ 기존 리소스를 절대 생성/수정/삭제하지 않음
# data source로 참조만 하여 EKS 등 신규 리소스에서 사용
#
# 기존 구조:
#   sko-vpc (10.0.0.0/16)  vpc-075deed66fcc7f348
#   ├── sko-public-subnet-a  (10.0.0.0/24, ap-northeast-2a)
#   ├── sko-private-subnet-b (10.0.1.0/24, ap-northeast-2b)
#   ├── sko-public-subnet-c  (10.0.2.0/24, ap-northeast-2c)
#   └── sko-public-subnet-b  (10.0.4.0/24, ap-northeast-2b)
#   + sko-internet-gateway (igw-06047fb95a448b7b4)
# =============================================================================

# ----- 기존 VPC 참조 -----

data "aws_vpc" "sko" {
  id = var.vpc_id
}

# ----- 기존 Subnet 참조 -----
# EKS는 최소 2개 AZ의 서브넷이 필요

data "aws_subnet" "eks_subnets" {
  for_each = toset(var.eks_subnet_ids)
  id       = each.value
}

# ----- NAT Gateway (신규 생성) -----
# EKS 워커 노드가 외부 인터넷에 접근하기 위해 필요
# (Bedrock API 호출, npm install, Docker image pull 등)
# 기존 VPC에 NAT Gateway가 없으므로 신규 생성

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = {
    Name = "${var.project_name}-nat-eip"
  }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = var.nat_gateway_subnet_id # public subnet에 배치

  tags = {
    Name = "${var.project_name}-nat"
  }
}

# ----- EKS 전용 Private Subnets (신규 생성) -----
# 기존 서브넷을 변경하지 않고, EKS 워커 노드 전용 private 서브넷을 새로 생성
# NAT Gateway를 통해 아웃바운드 인터넷 접근

resource "aws_subnet" "eks_private" {
  count = length(var.eks_private_subnet_cidrs)

  vpc_id            = data.aws_vpc.sko.id
  cidr_block        = var.eks_private_subnet_cidrs[count.index]
  availability_zone = var.eks_private_subnet_azs[count.index]

  tags = {
    Name = "${var.project_name}-eks-private-${var.eks_private_subnet_azs[count.index]}"
    "kubernetes.io/role/internal-elb"                = "1"
    "kubernetes.io/cluster/${var.project_name}-eks"   = "shared"
  }
}

# ----- Route Table (신규, EKS private subnets 전용) -----

resource "aws_route_table" "eks_private" {
  vpc_id = data.aws_vpc.sko.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-eks-private-rt"
  }
}

resource "aws_route_table_association" "eks_private" {
  count          = length(aws_subnet.eks_private)
  subnet_id      = aws_subnet.eks_private[count.index].id
  route_table_id = aws_route_table.eks_private.id
}
