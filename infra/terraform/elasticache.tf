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

# Phase 0 standalone cluster (bedrock-claude-redis) — Phase 1b (2026-04-13)에서 destroy.
# 이후 main_tls (aws_elasticache_replication_group.main_tls) 단독 운영.
#
# 기존 aws_elasticache_cluster.redis (cache.t3.micro, 단일 노드) 및 관련 outputs 삭제.
# auth-gateway / usage-worker / openwebui-pipelines 는 Phase 1a Task 3 완료 시점에
# 이미 main_tls (rediss://) 로 전환됨 (refs: 003ecc5, 462e642).

# =============================================================================
# Phase 1a: ElastiCache HA + TLS Replication Group
#
# 기존 Phase 0 standalone(bedrock-claude-redis)과 공존.
# Task 3에서 K8s manifest를 이 cluster로 포인팅 후 수동 cutover.
#
# 주요 변경:
#   - transit_encryption_enabled: true  → TLS (rediss://)
#   - at_rest_encryption_enabled: true  → 저장 시 암호화 (AWS 관리 키)
#   - auth_token: random 64자           → AUTH 명령 기반 인증
#   - num_cache_clusters: 2             → Multi-AZ HA (automatic failover)
#   - multi_az_enabled: true            → 다른 AZ에 replica 강제 배치
#   - node_type: cache.t3.small         → Phase 0 t3.micro보다 여유 용량
#                                         (Phase 1b cache.t3.medium 업그레이드 예정)
# =============================================================================

resource "random_password" "redis_auth_token" {
  length  = 64
  special = false # AWS ElastiCache auth_token: 16-128 chars, no symbols
  upper   = true
  lower   = true
  numeric = true
}

resource "aws_elasticache_replication_group" "main_tls" {
  # Phase 1a 신규 HA+TLS. 기존 standalone(aws_elasticache_cluster.redis)와 공존.
  replication_group_id = "${var.project_name}-redis-tls"
  description          = "${var.project_name} Redis HA + TLS (Phase 1a)"

  # Phase 1b 업그레이드 예정: cache.t3.medium (jti blacklist + Stream 여유 확보)
  node_type                  = "cache.t3.small"
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true # 다른 AZ 강제 배치 (automatic_failover만으론 불충분)

  engine               = "redis"
  engine_version       = "7.1"
  parameter_group_name = "default.redis7"

  port = 6379

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
  # kms_key_id omitted: AWS 관리 키(aws/elasticache) 사용.
  # Phase 1b ISMS-P 대응 시 고객 관리 CMK(aws_kms_key.redis)로 전환 예정.
  auth_token = random_password.redis_auth_token.result

  # PIPA 최소 보관 기준 7일. Phase 1b에서 30일로 상향 예정.
  snapshot_retention_limit = 7
  snapshot_window          = "03:00-04:00"
  # maintenance_window는 snapshot_window(03:00-04:00 UTC)와 겹치지 않게 고정
  maintenance_window = "sun:05:00-sun:06:00"

  tags = {
    Name    = "${var.project_name}-redis-tls"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }

  # auth_token은 수동 rotation 시점에만 교체.
  # random_password 재생성이 replication group 강제 교체를 유발하지 않도록 lock.
  lifecycle {
    ignore_changes = [auth_token]
  }
}

output "redis_tls_primary_endpoint" {
  description = "TLS Redis primary endpoint (Phase 1a) — rediss://{endpoint}:6379/0"
  value       = aws_elasticache_replication_group.main_tls.primary_endpoint_address
}

output "redis_tls_auth_token" {
  description = "TLS Redis AUTH token — K8s Secret으로 주입 (plaintext 노출 금지)"
  value       = random_password.redis_auth_token.result
  sensitive   = true
}
