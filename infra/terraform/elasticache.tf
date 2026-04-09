# =============================================================================
# ElastiCache Redis: auth-gateway 분산 락 + rate limiter 백엔드
#
# auth-gateway가 여러 프로세스/레플리카로 실행될 때 스케줄러 중복 실행을
# 방지하기 위한 분산 락(distributed lock) 저장소.
#
# 동작 방식:
#   1. 단일 노드 Redis (cache.t3.micro) — 최소 비용 구성
#   2. EKS Private Subnet에 배치 → Pod에서만 접근 가능
#   3. Security Group으로 EKS 노드 → Redis 간 6379 트래픽만 허용
#   4. auth-gateway는 redis_url 환경 변수로 연결
#   5. Redis 없으면 인메모리 fallback (단일 프로세스 환경)
#
# 비용 참고:
#   cache.t3.micro = ~$0.017/hr ≈ $12/month (ap-northeast-2 기준)
#   Serverless 대비 비용 예측이 쉽고, MVP 규모에서는 단일 노드로 충분
# =============================================================================

# ----- Redis Subnet Group -----
# ElastiCache 노드를 EKS 전용 Private Subnet에 배치
# 같은 서브넷에 있어야 Pod → Redis 간 레이턴시가 최소화됨

resource "aws_elasticache_subnet_group" "redis" {
  name        = "${var.project_name}-redis-subnet"
  description = "ElastiCache Redis subnet group - EKS private subnets"

  # EKS 워커 노드와 동일한 private 서브넷 사용
  # vpc.tf에서 생성한 eks_private 서브넷 (10.0.10.0/24, 10.0.20.0/24)
  subnet_ids = aws_subnet.eks_private[*].id

  tags = {
    Name    = "${var.project_name}-redis-subnet"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }
}

# ----- Redis Security Group -----
# EKS private 서브넷에서만 Redis(6379) 접근 허용
# EFS Security Group(efs.tf)과 동일한 CIDR 기반 패턴 사용
# → EKS managed node group의 SG는 AWS가 자동 관리하므로 CIDR이 더 안정적

resource "aws_security_group" "redis" {
  name_prefix = "${var.project_name}-redis-"
  vpc_id      = data.aws_vpc.sko.id
  description = "ElastiCache Redis - allow access from EKS private subnets"

  # Inbound: EKS private 서브넷에서 Redis(6379) 접근 허용
  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = var.eks_private_subnet_cidrs  # ["10.0.10.0/24", "10.0.20.0/24"]
    description = "Redis from EKS private subnets"
  }

  # Outbound: 전체 허용 (Redis 응답 트래픽)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-redis"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }
}

# ----- EKS → Redis 아웃바운드 규칙 -----
# EKS 클러스터 보안 그룹에서 Redis로 나가는 6379 트래픽을 명시적으로 허용
# efs.tf의 eks_to_efs 규칙과 동일한 패턴

resource "aws_security_group_rule" "eks_to_redis" {
  type                     = "egress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.redis.id
  security_group_id        = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  description              = "EKS nodes to ElastiCache Redis"
}

# ----- ElastiCache Redis Cluster -----
# 단일 노드 구성 (num_cache_nodes=1) — MVP/개발 환경에 적합
# 프로덕션 확장 시 Replication Group으로 마이그레이션 가능
#
# cache.t3.micro 사양:
#   vCPU: 2, 메모리: 0.5GB, 네트워크: Up to 5 Gbps
#   분산 락 + rate limiter 용도로 충분

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${var.project_name}-redis"
  engine               = "redis"
  engine_version       = "7.0"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379

  # 네트워크: EKS private 서브넷에 배치
  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  # 유지보수 윈도우: KST 기준 새벽 시간대 (UTC 일요일 18:00-19:00 = KST 월요일 03:00-04:00)
  maintenance_window = "sun:18:00-sun:19:00"

  # 스냅샷: 분산 락 용도이므로 백업 불필요 (비용 절감)
  snapshot_retention_limit = 0

  tags = {
    Name    = "${var.project_name}-redis"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }
}

# ----- Outputs -----

output "redis_endpoint" {
  description = "ElastiCache Redis 엔드포인트 (auth-gateway REDIS_URL에 사용)"
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "redis_port" {
  description = "ElastiCache Redis 포트"
  value       = aws_elasticache_cluster.redis.port
}

output "redis_connection_url" {
  description = "auth-gateway에 설정할 REDIS_URL 값 (redis://host:port/0)"
  value       = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:${aws_elasticache_cluster.redis.port}/0"
}
