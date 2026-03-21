# =============================================================================
# VPC + 네트워크 인프라
#
# 구조:
#   VPC (10.0.0.0/16)
#   ├── Public Subnet A  (10.0.1.0/24)  ← NAT Gateway, ALB
#   ├── Public Subnet C  (10.0.2.0/24)  ← ALB (이중화)
#   ├── Private Subnet A (10.0.10.0/24) ← EKS 워커 노드, Pod
#   └── Private Subnet C (10.0.20.0/24) ← EKS 워커 노드, Pod
#
# EKS 워커 노드는 Private Subnet에 배치하여 외부 직접 접근 차단.
# NAT Gateway를 통해 외부 인터넷 접근 (npm install, Docker pull 등).
# =============================================================================

# ----- VPC -----

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true # EKS가 내부 DNS 사용
  enable_dns_support   = true

  tags = {
    Name = "${var.project_name}-vpc"
  }
}

# ----- Internet Gateway -----
# VPC가 인터넷과 통신하기 위한 게이트웨이

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-igw"
  }
}

# ----- Public Subnets -----
# ALB(로드 밸런서)와 NAT Gateway가 위치하는 서브넷
# 외부에서 접근 가능한 영역

resource "aws_subnet" "public" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index + 1) # 10.0.1.0/24, 10.0.2.0/24
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project_name}-public-${var.availability_zones[count.index]}"
    # EKS가 이 서브넷에 ALB를 생성할 수 있도록 태그 지정
    "kubernetes.io/role/elb"                              = "1"
    "kubernetes.io/cluster/${var.project_name}-eks" = "shared"
  }
}

# ----- Private Subnets -----
# EKS 워커 노드와 Pod이 실행되는 서브넷
# 외부에서 직접 접근 불가, NAT Gateway를 통해서만 아웃바운드 가능

resource "aws_subnet" "private" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, (count.index + 1) * 10) # 10.0.10.0/24, 10.0.20.0/24
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project_name}-private-${var.availability_zones[count.index]}"
    # EKS가 이 서브넷에 내부 LB를 생성할 수 있도록 태그 지정
    "kubernetes.io/role/internal-elb"                      = "1"
    "kubernetes.io/cluster/${var.project_name}-eks" = "shared"
  }
}

# ----- NAT Gateway -----
# Private Subnet의 리소스가 외부 인터넷에 접근할 때 사용
# (예: Claude Code가 Bedrock API 호출, npm install 등)
# 비용 절감을 위해 1개만 생성 (프로덕션에서는 AZ별 1개 권장)

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = {
    Name = "${var.project_name}-nat-eip"
  }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id # 첫 번째 Public Subnet에 배치

  tags = {
    Name = "${var.project_name}-nat"
  }

  depends_on = [aws_internet_gateway.main]
}

# ----- Route Tables -----

# Public 라우트 테이블: 인터넷 게이트웨이로 직접 라우팅
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-public-rt"
  }
}

# Private 라우트 테이블: NAT Gateway를 통해 아웃바운드만 허용
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-private-rt"
  }
}

# 서브넷에 라우트 테이블 연결
resource "aws_route_table_association" "public" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
