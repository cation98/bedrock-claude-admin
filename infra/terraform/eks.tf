# =============================================================================
# EKS (Elastic Kubernetes Service) 클러스터
#
# EKS = AWS가 관리하는 Kubernetes 서비스
# - Control Plane: AWS가 관리 (API 서버, etcd 등)
# - Worker Nodes: 우리가 관리 (EC2 인스턴스에서 Pod 실행)
#
# Managed Node Group을 사용하면 노드(EC2)의 생성/업데이트/삭제도
# AWS가 자동 관리해줌 → 운영 부담 최소화
# =============================================================================

# ----- EKS 클러스터용 IAM Role -----
# EKS Control Plane이 AWS 리소스를 관리하기 위한 권한

resource "aws_iam_role" "eks_cluster" {
  name = "${var.project_name}-eks-cluster-role"

  # "EKS 서비스가 이 역할을 사용할 수 있다"는 신뢰 정책
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "eks.amazonaws.com"
      }
    }]
  })
}

# AWS 관리형 정책 연결 - EKS 클러스터 운영에 필요한 권한
resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

# ----- EKS 클러스터 -----

resource "aws_eks_cluster" "main" {
  name     = "${var.project_name}-eks"
  version  = var.eks_cluster_version
  role_arn = aws_iam_role.eks_cluster.arn

  # 클러스터 네트워크 설정
  vpc_config {
    # 기존 SKO VPC의 서브넷 사용 (Control Plane ENI 배치)
    subnet_ids = var.eks_subnet_ids

    # kubectl 접근 허용 (개발 단계에서는 public 허용)
    endpoint_public_access  = true
    endpoint_private_access = true
  }

  # OIDC Provider 활성화 (IRSA: Pod에 IAM 역할 부여 시 필요)
  # Pod 단위로 "이 Pod만 Bedrock API를 호출할 수 있다"를 설정할 수 있음
  access_config {
    authentication_mode = "API_AND_CONFIG_MAP"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
  ]
}

# ----- OIDC Provider -----
# IRSA (IAM Roles for Service Accounts)를 위한 OIDC Provider
# K8s ServiceAccount에 IAM Role을 연결할 수 있게 해주는 핵심 설정
# → Pod이 AWS 서비스(Bedrock)에 접근할 때 임시 자격증명을 자동 주입

data "tls_certificate" "eks" {
  url = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks.certificates[0].sha1_fingerprint]
  url             = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

# ----- Worker Node IAM Role -----
# EC2 워커 노드가 EKS 클러스터에 참여하고 ECR에서 이미지를 가져오기 위한 권한

resource "aws_iam_role" "eks_nodes" {
  name = "${var.project_name}-eks-node-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })
}

# 노드에 필요한 AWS 관리형 정책들
resource "aws_iam_role_policy_attachment" "eks_worker_node_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_cni_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_container_registry" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.eks_nodes.name
}

# ----- Cluster Autoscaler IRSA Role -----
# IRSA = ServiceAccount에 IAM Role 연결
# kube-system:cluster-autoscaler SA만 이 역할을 사용 가능

resource "aws_iam_role" "cluster_autoscaler" {
  name = "${var.project_name}-cluster-autoscaler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.eks.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:kube-system:cluster-autoscaler"
          "${replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "cluster_autoscaler" {
  name = "${var.project_name}-cluster-autoscaler"
  role = aws_iam_role.cluster_autoscaler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ClusterAutoscalerDescribe"
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingGroups",
          "autoscaling:DescribeAutoScalingInstances",
          "autoscaling:DescribeLaunchConfigurations",
          "autoscaling:DescribeScalingActivities",
          "autoscaling:DescribeTags",
          "ec2:DescribeImages",
          "ec2:DescribeInstanceTypes",
          "ec2:DescribeLaunchTemplateVersions",
          "ec2:GetInstanceTypesFromInstanceRequirements",
          "eks:DescribeNodegroup"
        ]
        Resource = "*"
      },
      {
        Sid    = "ClusterAutoscalerModify"
        Effect = "Allow"
        Action = [
          "autoscaling:SetDesiredCapacity",
          "autoscaling:TerminateInstanceInAutoScalingGroup"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/k8s.io/cluster-autoscaler/${var.project_name}-eks" = "owned"
          }
        }
      }
    ]
  })
}

# ----- Managed Node Group -----
# AWS가 자동 관리하는 워커 노드 그룹
# Auto Scaling으로 부하에 따라 노드 수 자동 조절

resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${var.project_name}-nodes"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = aws_subnet.eks_private[*].id # 신규 생성한 EKS 전용 private 서브넷

  instance_types = var.eks_node_instance_types

  scaling_config {
    desired_size = var.eks_node_desired_size
    min_size     = var.eks_node_min_size
    max_size     = var.eks_node_max_size
  }

  # 노드 업데이트 시 한 번에 1개씩 교체 (서비스 중단 최소화)
  update_config {
    max_unavailable = 1
  }

  labels = {
    role = "claude-terminal"
  }

  # Cluster Autoscaler 자동 탐색 태그
  # 이 태그가 ASG에 전파되어 Autoscaler가 관리 대상으로 인식
  tags = {
    "k8s.io/cluster-autoscaler/enabled"                 = "true"
    "k8s.io/cluster-autoscaler/${var.project_name}-eks" = "owned"
    Owner                                               = "N1102359"
    Env                                                 = var.environment
    Service                                             = "sko-claude-ai-agent"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry,
  ]
}

# ----- System Node Group (t3.large) — auth-gateway 전용 pair -----
# CLAUDE.md 설계 제약: auth-gateway 전용 2노드 pair (hard anti-affinity)
# - auth-gateway replica 1, 2를 각각 별개 노드에 배치 (동일 노드 불가)
# - ingress-nginx는 별도 ingress-workers 그룹으로 분리됨 (Phase 0 신설)
#
# 노드 taint: dedicated=system:NoSchedule
#   → auth-gateway Pod만 toleration으로 허용, 다른 Pod는 이 노드에 스케줄 불가
# 이 taint/toleration 설정은 infra/k8s/ 매니페스트(k8s팀 T6)와 대응됨

resource "aws_eks_node_group" "system" {
  cluster_name    = aws_eks_cluster.main.name
  # CLAUDE.md + k8s 팀 합의 naming: "system-node-large" (prefix 없음)
  # T6 manifest nodeSelector/toleration과 일치해야 함
  node_group_name = "system-node-large"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = aws_subnet.eks_private[*].id

  instance_types = var.eks_system_node_instance_types

  scaling_config {
    desired_size = var.eks_system_node_desired_size
    min_size     = var.eks_system_node_min_size
    max_size     = var.eks_system_node_max_size
  }

  update_config {
    max_unavailable = 1
  }

  # K8s 레이블: 이 노드에 스케줄될 Pod의 nodeSelector와 대응
  labels = {
    role = "system"
  }

  # taint: auth-gateway Pod 외 다른 워크로드 차단
  # K8s 매니페스트에서 toleration으로 명시한 Pod만 이 노드에 배치 가능
  taint {
    key    = "dedicated"
    value  = "system"
    effect = "NO_SCHEDULE"
  }

  # Cluster Autoscaler 자동 탐색 태그
  tags = {
    "k8s.io/cluster-autoscaler/enabled"                 = "true"
    "k8s.io/cluster-autoscaler/${var.project_name}-eks" = "owned"
    Name                                                = "system-node-large"
    Owner                                               = "N1102359"
    Env                                                 = var.environment
    Service                                             = "sko-claude-ai-agent"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry,
  ]
}

# ----- Ingress Node Group (t3.large) — ingress-nginx 전용 -----
# Phase 0 신설: ingress-nginx를 system 노드에서 분리하여 전용 그룹으로 이동
# 이유: Open WebUI WebSocket 스트리밍(2000명 규모) 트래픽 대응을 위해
#       ingress 레이어를 system 노드와 독립적으로 수평 확장 필요
#
# min=2: HA 필수 (ingress 장애 시 전체 트래픽 차단)
# max=6: 500 concurrent 세션 × WebSocket keepalive 고려한 상한
#
# 노드 taint: dedicated=ingress:NoSchedule
#   → ingress-nginx Pod만 이 노드에 스케줄 가능

resource "aws_eks_node_group" "ingress" {
  cluster_name    = aws_eks_cluster.main.name
  # CLAUDE.md + k8s 팀 합의 naming: "ingress-workers" (prefix 없음)
  node_group_name = "ingress-workers"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = aws_subnet.eks_private[*].id

  instance_types = var.eks_ingress_node_instance_types

  scaling_config {
    desired_size = var.eks_ingress_node_desired_size
    min_size     = var.eks_ingress_node_min_size
    max_size     = var.eks_ingress_node_max_size
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    role = "ingress"
  }

  taint {
    key    = "dedicated"
    value  = "ingress"
    effect = "NO_SCHEDULE"
  }

  tags = {
    "k8s.io/cluster-autoscaler/enabled"                 = "true"
    "k8s.io/cluster-autoscaler/${var.project_name}-eks" = "owned"
    Name                                                = "ingress-workers"
    Owner                                               = "N1102359"
    Env                                                 = var.environment
    Service                                             = "sko-claude-ai-agent"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry,
  ]
}

# ----- 1:1 전용 Node Group (t3.medium) -----
# 사용자별 노드 1대 전용 할당 — Pod 간 리소스 간섭 원천 제거
# Pod Anti-Affinity(k8s_service.py)와 함께 1-node-1-pod 모델 구현
# Phase 2 (2000명): 개발자 200명 × 50% 동시 접속 = 100 max

resource "aws_eks_node_group" "dedicated" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${var.project_name}-dedicated-nodes"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = aws_subnet.eks_private[*].id

  instance_types = var.eks_dedicated_node_instance_types

  scaling_config {
    desired_size = var.eks_dedicated_node_desired_size
    min_size     = var.eks_dedicated_node_min_size
    max_size     = var.eks_dedicated_node_max_size
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    role = "claude-dedicated"
  }

  disk_size = 30

  tags = {
    "k8s.io/cluster-autoscaler/enabled"                 = "true"
    "k8s.io/cluster-autoscaler/${var.project_name}-eks" = "owned"
    Owner                                               = "N1102359"
    Env                                                 = var.environment
    Service                                             = "sko-claude-ai-agent"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry,
  ]
}
