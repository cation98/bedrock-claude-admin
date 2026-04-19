# =============================================================================
# Gitea RDS: PostgreSQL 전용 데이터베이스
#
# 역할:
#   Gitea CE의 백엔드 저장소. 플러그인 마켓플레이스 미러 메타데이터,
#   사용자 개인 레포지터리 정보, Gitea 내부 설정을 저장.
#
# 설계 결정:
#   - shared RDS 미사용: Gitea 스키마 격리 및 독립 운용을 위해 전용 인스턴스
#   - db.t3.micro: MVP Phase 1 — 플러그인 수백 개 + 사용자 200명 기준 충분
#   - Single-AZ: Phase 1 MVP. HA 필요 시 Phase 2에서 Multi-AZ 전환
#   - 보안: EKS private subnet CIDR에서만 5432 접근 (EFS/Redis와 동일 패턴)
#
# Terraform 적용 시 반드시 -target 사용 (subnet drift 방지):
#   export TF_VAR_gitea_db_password=$(op item get <1password-item-id> --fields password --reveal)
#   terraform apply \
#     -target=aws_db_subnet_group.gitea \
#     -target=aws_security_group.gitea_db \
#     -target=aws_security_group_rule.eks_to_gitea_db \
#     -target=aws_db_instance.gitea
# =============================================================================

# ----- RDS Subnet Group -----
# EKS 전용 private 서브넷에 Gitea DB 배치 (Pod와 동일 네트워크 세그먼트)

resource "aws_db_subnet_group" "gitea" {
  name        = "${var.project_name}-gitea-db-subnet"
  description = "Gitea RDS subnet group - EKS private subnets"
  subnet_ids  = aws_subnet.eks_private[*].id

  tags = {
    Name    = "${var.project_name}-gitea-db-subnet"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }
}

# ----- Gitea DB Security Group -----
# EKS private 서브넷에서만 PostgreSQL(5432) 접근 허용
# EFS/Redis와 동일한 CIDR 기반 패턴 (EKS managed node SG는 AWS 자동 관리로 불안정)

resource "aws_security_group" "gitea_db" {
  name_prefix = "${var.project_name}-gitea-db-"
  vpc_id      = data.aws_vpc.sko.id
  description = "Gitea RDS - allow PostgreSQL from EKS private subnets"

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.eks_private_subnet_cidrs
    description = "PostgreSQL from EKS private subnets"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-gitea-db"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }
}

# ----- EKS → Gitea DB 아웃바운드 규칙 -----
# EKS 클러스터 보안 그룹에서 Gitea DB로 나가는 5432 트래픽 허용 (EFS/Redis와 동일 패턴)

resource "aws_security_group_rule" "eks_to_gitea_db" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.gitea_db.id
  security_group_id        = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  description              = "EKS nodes to Gitea RDS PostgreSQL"
}

# ----- Gitea RDS Instance -----

resource "aws_db_instance" "gitea" {
  identifier = "${var.project_name}-gitea"

  engine         = "postgres"
  engine_version = "15.10"
  instance_class = "db.t3.micro"

  allocated_storage     = 20
  max_allocated_storage = 100  # 자동 스케일링 상한 (GB)
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "gitea"
  username = "gitea"
  password = var.gitea_db_password

  db_subnet_group_name   = aws_db_subnet_group.gitea.name
  vpc_security_group_ids = [aws_security_group.gitea_db.id]

  # MVP Single-AZ (Phase 2에서 multi_az = true 전환 예정)
  multi_az            = false
  publicly_accessible = false

  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "sun:05:00-sun:06:00"

  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.project_name}-gitea-final"
  deletion_protection       = true

  tags = {
    Name    = "${var.project_name}-gitea"
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }
}

# ----- Outputs -----

output "gitea_db_endpoint" {
  description = "Gitea RDS endpoint — Helm values.yaml의 gitea.database.host에 사용"
  value       = aws_db_instance.gitea.endpoint
}

output "gitea_db_name" {
  description = "Gitea DB 이름"
  value       = aws_db_instance.gitea.db_name
}
