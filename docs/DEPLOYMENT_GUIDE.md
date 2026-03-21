# Deployment Guide

AWS Bedrock Claude Code 플랫폼 배포 가이드.

## Prerequisites

아래 도구가 설치되어 있어야 합니다:

```bash
# AWS CLI
brew install awscli

# Terraform
brew install terraform

# kubectl (Kubernetes CLI)
brew install kubectl

# Docker Desktop
# https://docs.docker.com/desktop/install/mac-install/
```

## Step 1: AWS 인증

```bash
# SSO 로그인 (또는 IAM 키 설정)
aws configure sso --profile bedrock-claude
aws sso login --profile bedrock-claude

# 인증 확인
aws sts get-caller-identity --profile bedrock-claude
```

## Step 2: Terraform으로 인프라 생성

```bash
cd infra/terraform

# 변수 파일 설정
cp terraform.tfvars.example terraform.tfvars
# terraform.tfvars 편집 (리전, 노드 수 등)

# 초기화 및 배포
terraform init
terraform plan    # 생성될 리소스 확인
terraform apply   # 실제 생성 (약 15-20분)
```

**생성되는 AWS 리소스:**
- VPC + Subnets + NAT Gateway
- EKS Cluster + Managed Node Group (m5.large × 2)
- ECR Repository
- IAM Roles (Bedrock access via IRSA)

## Step 3: Docker 이미지 빌드 & Push

```bash
# ECR URL 확인
cd infra/terraform
ECR_URL=$(terraform output -raw ecr_repository_url)

# ECR 로그인
aws ecr get-login-password --region ap-northeast-2 | \
    docker login --username AWS --password-stdin $ECR_URL

# AMD64 아키텍처로 빌드 (EKS 노드가 x86_64)
cd ../../container-image
docker build --platform linux/amd64 -t claude-code-terminal .

# 태깅 및 Push
docker tag claude-code-terminal:latest $ECR_URL:latest
docker push $ECR_URL:latest
```

## Step 4: Kubernetes 배포

```bash
# kubeconfig 설정
CLUSTER_NAME=$(cd infra/terraform && terraform output -raw eks_cluster_name)
aws eks update-kubeconfig --name $CLUSTER_NAME --region ap-northeast-2

# 클러스터 연결 확인
kubectl get nodes

# Namespace 생성
kubectl apply -f infra/k8s/namespace.yaml

# RDS Secret 설정
cp infra/k8s/rds-secret.yaml.example infra/k8s/rds-secret.yaml
# rds-secret.yaml 편집: 실제 DB URL을 base64 인코딩하여 입력
# echo -n "postgresql://user:pass@host:5432/db" | base64
kubectl apply -f infra/k8s/rds-secret.yaml

# ServiceAccount 배포 (Bedrock 접근 권한)
# service-account.yaml의 role-arn을 실제 값으로 교체:
# terraform output bedrock_role_arn
kubectl apply -f infra/k8s/service-account.yaml

# NetworkPolicy 배포 (Pod 간 격리)
kubectl apply -f infra/k8s/network-policy.yaml
```

## Step 5: 테스트 Pod 실행

```bash
# pod-template.yaml의 이미지 URL을 실제 ECR URL로 수정 후:
kubectl apply -f infra/k8s/pod-template.yaml

# Pod 상태 확인
kubectl get pods -n claude-sessions

# Pod 로그 확인
kubectl logs -n claude-sessions claude-terminal-test

# 포트 포워딩으로 로컬에서 접속
kubectl port-forward -n claude-sessions claude-terminal-test 7681:7681

# 브라우저에서 http://localhost:7681 접속
```

## Step 6: 테스트 Pod 정리

```bash
kubectl delete pod claude-terminal-test -n claude-sessions
```

## 자동 배포 스크립트

위 모든 단계를 한 번에 실행:

```bash
./scripts/deploy.sh          # 전체 배포
./scripts/deploy.sh infra    # Terraform만
./scripts/deploy.sh image    # Docker 이미지만
./scripts/deploy.sh k8s      # K8s manifests만
```

## Troubleshooting

### EKS 노드가 NotReady

```bash
kubectl get nodes
kubectl describe node <node-name>
# Security Group에서 노드 간 통신이 허용되어 있는지 확인
```

### Pod이 ImagePullBackOff

```bash
kubectl describe pod <pod-name> -n claude-sessions
# ECR URL이 올바른지, 노드의 IAM Role에 ECR 읽기 권한이 있는지 확인
```

### Bedrock 접근 불가

```bash
# Pod 내부에서 확인
kubectl exec -it <pod-name> -n claude-sessions -- aws sts get-caller-identity
# IRSA가 제대로 설정되었는지 ServiceAccount의 annotation 확인
```

## 비용 참고

| 리소스 | Phase 1 (15명) | 월 예상 비용 |
|--------|---------------|-------------|
| EKS Cluster | 1개 | ~$73 |
| m5.large × 2 | On-Demand | ~$140 |
| NAT Gateway | 1개 | ~$45 |
| ECR Storage | <1GB | ~$0.10 |
| Bedrock (Sonnet 4.6) | 사용량 기반 | 변동 |
| **합계 (인프라)** | | **~$258/월** |

> 실습 기간(1주)만 운영하면 약 $65 수준.
> 사용하지 않을 때는 노드를 0으로 스케일 다운하여 비용 절감 가능.
