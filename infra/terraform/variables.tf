# =============================================================================
# 입력 변수 정의
# 실제 값은 terraform.tfvars 파일에서 설정
# =============================================================================

variable "aws_region" {
  description = "AWS 리전 (서울)"
  type        = string
  default     = "ap-northeast-2"
}

variable "environment" {
  description = "환경 이름 (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "프로젝트 이름 (리소스 네이밍에 사용)"
  type        = string
  default     = "bedrock-claude"
}

# ----- 기존 SKO VPC (data source 참조) -----

variable "vpc_id" {
  description = "기존 SKO VPC ID (절대 수정/삭제하지 않음)"
  type        = string
  default     = "vpc-075deed66fcc7f348"
}

variable "eks_subnet_ids" {
  description = "EKS Control Plane이 사용할 기존 서브넷 (최소 2개 AZ)"
  type        = list(string)
  default = [
    "subnet-03f741587efae3ffb", # sko-public-subnet-a (ap-northeast-2a)
    "subnet-02a58f3358354e490", # sko-public-subnet-b (ap-northeast-2b)
    "subnet-083d39916aeea24cd", # sko-public-subnet-c (ap-northeast-2c)
  ]
}

variable "private_route_table_id" {
  description = "기존 private route table (NAT Instance + Bedrock Endpoint 라우팅 포함)"
  type        = string
  default     = "rtb-0700167f652e4360a" # sko-private-subnet-rt
}

# ----- EKS 전용 Private Subnets (신규 생성) -----

variable "eks_private_subnet_cidrs" {
  description = "EKS 워커 노드 전용 private 서브넷 CIDR (신규 생성)"
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.20.0/24"]
}

variable "eks_private_subnet_azs" {
  description = "EKS private 서브넷 가용영역"
  type        = list(string)
  default     = ["ap-northeast-2a", "ap-northeast-2c"]
}

# ----- EKS -----

variable "eks_cluster_version" {
  description = "EKS Kubernetes 버전"
  type        = string
  default     = "1.31"
}

variable "eks_node_instance_types" {
  description = "EKS 워커 노드 인스턴스 타입"
  type        = list(string)
  default     = ["m5.large"]
}

variable "eks_node_desired_size" {
  description = "워커 노드 희망 개수"
  type        = number
  default     = 2
}

variable "eks_node_min_size" {
  description = "워커 노드 최소 개수 (0 = 야간 완전 축소 가능)"
  type        = number
  default     = 0
}

variable "eks_node_max_size" {
  description = "워커 노드 최대 개수 (Phase 2: Open WebUI 10 + Bedrock AG 8 + 기타 서비스 고려)"
  type        = number
  default     = 15
}

# ----- 1:1 전용 노드그룹 (t3.xlarge, 사용자별 1 node) -----
# 2026-04-17: t3.medium → t3.large → 2026-04-20: t3.large → t3.xlarge (4 vCPU / 16 GiB)
# 현재 운용 nodegroup: bedrock-claude-dedicated-xlarge-nodes
# 구 nodegroup(bedrock-claude-dedicated-nodes, t3.large)은 CA scale-down 후 terraform 관리로 전환 예정

variable "eks_dedicated_node_instance_types" {
  description = "1:1 전용 노드 인스턴스 타입"
  type        = list(string)
  default     = ["t3.xlarge"]
}

variable "eks_dedicated_node_desired_size" {
  description = "1:1 전용 노드 희망 개수 (CA가 동적 조정 — lifecycle ignore_changes 적용)"
  type        = number
  default     = 0
}

variable "eks_dedicated_node_min_size" {
  description = "1:1 전용 노드 최소 개수 (0 = 야간 완전 축소 가능)"
  type        = number
  default     = 0
}

variable "eks_dedicated_node_max_size" {
  description = "1:1 전용 노드 최대 개수 (개발자 200명 × 50% 동시 = 100 max)"
  type        = number
  default     = 100
}

# ----- 시스템 노드 (auth-gateway 전용 pair) -----

variable "eks_system_node_instance_types" {
  description = "시스템 노드 인스턴스 타입 (auth-gateway 전용 pair)"
  type        = list(string)
  default     = ["t3.large"]
}

variable "eks_system_node_desired_size" {
  description = "시스템 노드 희망 개수 (최소 2 — auth-gateway HA pair)"
  type        = number
  default     = 2
}

variable "eks_system_node_min_size" {
  description = "시스템 노드 최소 개수 (항상 2 유지 — auth-gateway 무중단 필수)"
  type        = number
  default     = 2
}

variable "eks_system_node_max_size" {
  description = "시스템 노드 최대 개수 (auth-gateway HPA 대응, 동일 노드 anti-affinity 적용)"
  type        = number
  default     = 3
}

# ----- Ingress 노드 (ingress-nginx 전용) -----

variable "eks_ingress_node_instance_types" {
  description = "Ingress 노드 인스턴스 타입 (WebSocket 스트리밍 트래픽 대응)"
  type        = list(string)
  default     = ["t3.large"]
}

variable "eks_ingress_node_desired_size" {
  description = "Ingress 노드 희망 개수 (min=2 HA)"
  type        = number
  default     = 2
}

variable "eks_ingress_node_min_size" {
  description = "Ingress 노드 최소 개수 (항상 2 유지 — ingress HA 필수)"
  type        = number
  default     = 2
}

variable "eks_ingress_node_max_size" {
  description = "Ingress 노드 최대 개수 (Open WebUI WebSocket 2000명 대응, max=6)"
  type        = number
  default     = 6
}

# ----- Burst 노드 (spot m5.xlarge — Phase 1b 50명 burst 수용) -----
# desired=0 으로 평시에는 노드 없음 — Cluster Autoscaler가 HPA 부하에 따라 자동 확장
# SPOT 인스턴스 사용으로 On-Demand 대비 약 70% 비용 절감

variable "eks_burst_node_instance_types" {
  description = "burst-workers nodegroup spot instance types (multi-pool, AZ 가용성 확보)"
  type        = list(string)
  default     = ["m5.xlarge", "m5a.xlarge"]
}

variable "eks_burst_node_desired_size" {
  description = "burst-workers nodegroup desired size (Phase 1b: 0, HPA 발동 시 CA가 자동 scale-out)"
  type        = number
  default     = 0
}

variable "eks_burst_node_min_size" {
  description = "burst-workers nodegroup min size (0 = 평시 비용 없음)"
  type        = number
  default     = 0
}

variable "eks_burst_node_max_size" {
  description = "burst-workers nodegroup max size (Phase 1b: 50명 burst 흡수 — m5.xlarge × 4 = 충분)"
  type        = number
  default     = 4
}

# ----- Bedrock -----

variable "bedrock_region" {
  description = "Bedrock Claude 모델이 있는 리전"
  type        = string
  default     = "us-east-1"
}

# ----- Gitea -----

variable "gitea_db_password" {
  description = "Gitea Postgres password (sensitive — 1Password에서 주입, terraform.tfvars에 평문 저장 금지)"
  type        = string
  sensitive   = true
}

# ----- User Apps Workers Nodegroup -----

variable "eks_user_apps_instance_types" {
  description = "user-apps-workers: 사용자 배포 앱 전용 (bin-packing, t3.medium으로 고밀도 배치)"
  type        = list(string)
  default     = ["t3.medium"]
}

variable "eks_user_apps_desired_size" {
  description = "user-apps-workers 초기 노드 수 (Cluster Autoscaler가 동적 조정)"
  type        = number
  default     = 1
}

variable "eks_user_apps_min_size" {
  description = "user-apps-workers 최소 노드 수 (0 = 앱 없을 때 비용 절감)"
  type        = number
  default     = 0
}

variable "eks_user_apps_max_size" {
  description = "user-apps-workers 최대 노드 수"
  type        = number
  default     = 5
}

# ----- Gitea Workers Nodegroup (t3.large) — gitea-valkey + onlyoffice 전용 -----
# dedicated nodegroup에서 시스템 워크로드(valkey, onlyoffice)를 분리하여
# dedicated 노드가 사용자 세션이 없을 때 Cluster Autoscaler로 0까지 scale-down 가능하도록 함

variable "eks_gitea_node_instance_types" {
  description = "gitea-workers: valkey-cluster + onlyoffice 전용 (t3.large, allocatable ~1.9vCPU/5GiB)"
  type        = list(string)
  default     = ["t3.large"]
}

variable "eks_gitea_node_desired_size" {
  description = "gitea-workers 초기 노드 수"
  type        = number
  default     = 1
}

variable "eks_gitea_node_min_size" {
  description = "gitea-workers 최소 노드 수 (1 = valkey/onlyoffice 상시 운용)"
  type        = number
  default     = 1
}

variable "eks_gitea_node_max_size" {
  description = "gitea-workers 최대 노드 수 (valkey 3-node + onlyoffice, 2노드면 충분)"
  type        = number
  default     = 2
}
