#!/bin/bash
# =============================================================================
# 전체 배포 스크립트
#
# 사용법:
#   ./scripts/deploy.sh          # 전체 배포
#   ./scripts/deploy.sh infra    # Terraform만
#   ./scripts/deploy.sh image    # Docker 이미지만
#   ./scripts/deploy.sh k8s      # K8s manifests만
# =============================================================================

set -euo pipefail

# 색상 출력
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STEP="${1:-all}"

log()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ----- 사전 확인 -----
check_prerequisites() {
    log "Checking prerequisites..."

    command -v aws >/dev/null 2>&1 || error "AWS CLI not installed"
    command -v terraform >/dev/null 2>&1 || error "Terraform not installed"
    command -v kubectl >/dev/null 2>&1 || error "kubectl not installed"
    command -v docker >/dev/null 2>&1 || error "Docker not installed"

    # AWS 인증 확인
    aws sts get-caller-identity >/dev/null 2>&1 || error "AWS credentials not configured. Run: aws sso login"

    log "All prerequisites met."
}

# ----- Step 1: Terraform (인프라 프로비저닝) -----
deploy_infra() {
    log "Step 1: Deploying infrastructure with Terraform..."
    cd "${PROJECT_ROOT}/infra/terraform"

    if [ ! -f terraform.tfvars ]; then
        warn "terraform.tfvars not found. Copying from example..."
        cp terraform.tfvars.example terraform.tfvars
        warn "Please edit infra/terraform/terraform.tfvars with actual values, then re-run."
        exit 1
    fi

    terraform init
    terraform plan -out=tfplan
    echo ""
    warn "Review the plan above. Proceed? (yes/no)"
    read -r CONFIRM
    if [ "$CONFIRM" = "yes" ]; then
        terraform apply tfplan
        rm -f tfplan
        log "Infrastructure deployed."
    else
        warn "Cancelled."
        rm -f tfplan
        exit 0
    fi
}

# ----- Step 2: Docker 이미지 빌드 & ECR Push -----
deploy_image() {
    log "Step 2: Building and pushing Docker image..."
    cd "${PROJECT_ROOT}/infra/terraform"

    # Terraform output에서 ECR URL 가져오기
    ECR_URL=$(terraform output -raw ecr_repository_url 2>/dev/null) || error "Run 'deploy.sh infra' first"
    AWS_REGION=$(terraform output -raw 2>/dev/null | grep -A0 "aws_region" || echo "ap-northeast-2")

    cd "${PROJECT_ROOT}/container-image"

    # ECR 로그인
    aws ecr get-login-password --region ap-northeast-2 | \
        docker login --username AWS --password-stdin "${ECR_URL}"

    # 빌드 & Push
    docker build --platform linux/amd64 -t claude-code-terminal .
    docker tag claude-code-terminal:latest "${ECR_URL}:latest"
    docker push "${ECR_URL}:latest"

    log "Image pushed to ${ECR_URL}:latest"
}

# ----- Step 3: K8s Manifests 배포 -----
deploy_k8s() {
    log "Step 3: Deploying Kubernetes manifests..."
    cd "${PROJECT_ROOT}/infra/terraform"

    # kubeconfig 설정
    CLUSTER_NAME=$(terraform output -raw eks_cluster_name 2>/dev/null) || error "Run 'deploy.sh infra' first"
    aws eks update-kubeconfig --name "${CLUSTER_NAME}" --region ap-northeast-2

    cd "${PROJECT_ROOT}/infra/k8s"

    # Namespace 먼저 생성
    kubectl apply -f namespace.yaml

    # RDS Secret 확인
    if [ ! -f rds-secret.yaml ]; then
        warn "rds-secret.yaml not found. Copy from rds-secret.yaml.example and configure."
        warn "Skipping secret deployment."
    else
        kubectl apply -f rds-secret.yaml
    fi

    # ServiceAccount 배포
    kubectl apply -f service-account.yaml

    # NetworkPolicy 배포
    kubectl apply -f network-policy.yaml

    log "K8s manifests deployed."
    echo ""
    log "Test with: kubectl apply -f pod-template.yaml"
    log "Check: kubectl get pods -n claude-sessions"
}

# ----- 실행 -----
check_prerequisites

case "$STEP" in
    all)
        deploy_infra
        deploy_image
        deploy_k8s
        ;;
    infra)
        deploy_infra
        ;;
    image)
        deploy_image
        ;;
    k8s)
        deploy_k8s
        ;;
    *)
        error "Unknown step: $STEP. Use: all, infra, image, k8s"
        ;;
esac

echo ""
log "Deployment complete! 🎉"
