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
  subnet_ids      = aws_subnet.eks_private[*].id  # 신규 생성한 EKS 전용 private 서브넷

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
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
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
    Owner   = "N1102359"
    Env     = var.environment
    Service = "sko-claude-ai-agent"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry,
  ]
}
