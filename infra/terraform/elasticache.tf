# =============================================================================
# ElastiCache Redis: HA Replication Group (Phase 0 신설)
#
# 역할 (Phase 0 이후 확장):
#   1. auth-gateway 분산 락 + rate limiter 백엔드 (기존)
#   2. JWT jti 블랙리스트 (O(1) revocation 검증)
#      - access TTL 15분, refresh TTL 12시간 — blacklist SET에 저장
#      - auth-gateway HPA 2-8 replicas 전체가 공유하는 중앙 저장소
#   3. Redis Stream (stream:usage_events): Bedrock AG → usage-worker 비동기 큐
#      - Open WebUI Pipelines(usage_emit_pipeline.py)가 XADD 수행
#      - usage-worker Deployment가 consumer group으로 배치 INSERT
#   4. Budget reservation 임시 레코드 (pre-request 잔액 선점)
#
# 아키텍처 변경 (cache.t3.micro 단일 → cache.t3.medium Replication Group):
#   - Phase 1 이전에 HA 구성 필수 (Redis 장애 → 전체 인증 차단 리스크)
#   - Multi-AZ: primary(ap-northeast-2a) + replica(ap-northeast-2c)
#   - automatic_failover_enabled: primary 장애 시 replica 자동 승격
#   - cache.t3.medium (3.09 Gi): jti blacklist 2MB + Stream 5MB + 여유 3Gi
#
# 비용 참고:
#   cache.t3.medium = ~$0.068/hr × 2노드 ≈ $98/month (ap-northeast-2)
#   vs. cache.t3.micro 단일 ≈ $12/month
#   → HA + 용량 업그레이드로 ~$86/month 추가 (Phase 0 필수 투자)
#
# 연결 방법:
#   - 쓰기: primary_endpoint_address (auth-gateway, Pipelines)
#   - 읽기: reader_endpoint_address (optional read replica)
#   - 연결 URL: redis://{primary_endpoint}:6379/0
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
    cidr_blocks = var.eks_private_subnet_cidrs # ["10.0.10.0/24", "10.0.20.0/24"]
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

# ----- ElastiCache Redis cluster (managed resource — 복구 2026-04-12) -----
#
# 복구 경위:
#   이전에는 기존 cluster를 data source로만 참조했음.
#   terraform apply(drift 동기화) 중 state의 aws_elasticache_cluster.redis resource가
#   삭제되어 클러스터가 파괴됨. 재생성을 위해 managed resource로 전환.
#
# Phase 0 구성 (t3.micro 단일 노드) — 기존과 동일:
#   - engine: redis 7.x (AWS 기본 최신 7.x 사용)
#   - node_type: cache.t3.micro (기존 운용 중 사양)
#   - 단일 노드: num_cache_nodes=1 (Phase 1에서 HA 전환 예정)
#
# Phase 1 업그레이드 경로 (TODO):
#   1. bedrock-claude-redis-ha (t3.medium, 2노드 HA) 신규 생성
#   2. auth-gateway + Pipelines REDIS_URL 전환
#   3. 이 resource 삭제
#
# K8s 학습 노트: ElastiCache는 EKS Pod에서 직접 접근하는 외부 AWS 서비스.
#   security_group_ids로 네트워크 접근을 제어하고,
#   subnet_group_name으로 EKS private subnet에 배치한다.

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${var.project_name}-redis"
  engine               = "redis"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.1"
  port                 = 6379

  # EKS private subnet group — vpc.tf의 eks_private subnet에 배치
  subnet_group_name = aws_elasticache_subnet_group.redis.name
  # EKS Pod → Redis 6379 접근 허용 SG
  security_group_ids = [aws_security_group.redis.id]

  tags = {
    Name    = "${var.project_name}-redis"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }
}

# ----- Outputs -----

output "redis_primary_endpoint" {
  description = "Redis 엔드포인트 (auth-gateway, Pipelines)"
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "redis_reader_endpoint" {
  description = "Redis 엔드포인트 (Phase 0: primary와 동일 — standalone cluster)"
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "redis_port" {
  description = "Redis 포트"
  value       = aws_elasticache_cluster.redis.cache_nodes[0].port
}

output "redis_connection_url" {
  description = "REDIS_URL 값 (redis://endpoint:port/0) — auth-gateway 환경변수에 사용"
  value       = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:${aws_elasticache_cluster.redis.cache_nodes[0].port}/0"
}
