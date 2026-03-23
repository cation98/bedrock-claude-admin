# =============================================================================
# EFS: 사용자 워크스페이스 영속 스토리지
#
# 사용자별 1GB workspace를 제공하여 Pod 재시작 후에도 파일이 유지됩니다.
# EFS는 NFS 기반이라 여러 AZ의 Pod에서 동시 마운트 가능 (ReadWriteMany).
#
# 동작 방식:
#   1. EFS 파일시스템 1개를 생성 (탄력적 용량, 사용한 만큼만 과금)
#   2. 각 Private Subnet에 Mount Target 생성 (Pod이 NFS로 접근하는 진입점)
#   3. Security Group으로 EKS 노드 → EFS 간 NFS(2049) 트래픽만 허용
#   4. K8s에서는 EFS CSI Driver + PV/PVC로 Pod에 마운트
#   5. 각 사용자는 sub_path로 격리된 디렉토리 사용 (users/{username}/)
# =============================================================================

# ----- EFS 파일시스템 -----
# 모든 사용자의 workspace를 하나의 EFS에 저장 (sub_path로 격리)
# encrypted=true: 저장 데이터 암호화 (AWS managed key 사용)

resource "aws_efs_file_system" "user_workspaces" {
  creation_token = "${var.project_name}-user-workspaces"
  encrypted      = true

  # 30일간 접근하지 않은 파일은 IA(Infrequent Access) 계층으로 자동 이동
  # IA 계층은 스토리지 비용이 ~92% 저렴 (접근 시 소액 요금 발생)
  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = {
    Name        = "${var.project_name}-user-workspaces"
    Environment = var.environment
  }
}

# ----- Mount Targets -----
# 각 Private Subnet에 Mount Target을 생성해야 해당 AZ의 Pod이 EFS에 접근 가능
# Mount Target = EFS에 접근하기 위한 ENI(네트워크 인터페이스)
# 2개 AZ (ap-northeast-2a, 2c)에 각각 1개씩 생성

resource "aws_efs_mount_target" "eks_private" {
  count = length(aws_subnet.eks_private)

  file_system_id  = aws_efs_file_system.user_workspaces.id
  subnet_id       = aws_subnet.eks_private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

# ----- EFS Security Group -----
# NFS 프로토콜(TCP 2049)만 허용하는 최소 권한 보안 그룹
# EKS Private Subnet CIDR에서만 접근 허용

resource "aws_security_group" "efs" {
  name_prefix = "${var.project_name}-efs-"
  vpc_id      = data.aws_vpc.sko.id
  description = "EFS mount target - allow NFS from EKS private subnets"

  # Inbound: EKS private 서브넷에서 NFS(2049) 접근 허용
  # security_groups 대신 CIDR 사용 — EKS managed node group의 SG는
  # AWS가 자동 관리하므로 CIDR 기반이 더 안정적
  ingress {
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = var.eks_private_subnet_cidrs  # ["10.0.10.0/24", "10.0.20.0/24"]
    description = "NFS from EKS private subnets"
  }

  # Outbound: 전체 허용 (NFS 응답 트래픽)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.project_name}-efs"
    Environment = var.environment
  }
}

# ----- EKS → EFS NFS 아웃바운드 규칙 -----
# EKS 클러스터 보안 그룹에서 EFS로 나가는 NFS 트래픽을 명시적으로 허용
# EKS 클러스터 SG는 aws_eks_cluster가 자동 생성하며 vpc_config에서 참조 가능

resource "aws_security_group_rule" "eks_to_efs" {
  type                     = "egress"
  from_port                = 2049
  to_port                  = 2049
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.efs.id
  security_group_id        = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  description              = "EKS nodes → EFS (NFS)"
}

# ----- Outputs -----

output "efs_file_system_id" {
  description = "EFS filesystem ID (K8s PV의 volumeHandle에 사용)"
  value       = aws_efs_file_system.user_workspaces.id
}

output "efs_dns_name" {
  description = "EFS DNS name (디버깅용)"
  value       = aws_efs_file_system.user_workspaces.dns_name
}
