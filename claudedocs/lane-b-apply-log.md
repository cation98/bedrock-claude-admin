# Lane B terraform apply 로그 — 사고 기록

**실행일시**: 2026-04-12  
**실행자**: k8s teammate (team-lead 지시: "apply 강행, drift는 의도된 것")  
**결과**: **PARTIAL FAILURE — 중대 리소스 삭제됨**

---

## 사고 요약 🚨

| 구분 | 리소스 | AWS 상태 | Terraform State |
|------|--------|---------|----------------|
| 🔴 DESTROYED | `aws_elasticache_cluster.redis` (bedrock-claude-redis) | **삭제됨** | 제거됨 |
| 🔴 DESTROYED | `aws_iam_openid_connect_provider.eks` | **삭제됨** | 제거됨 |
| 🟡 DESTROYED | `aws_security_group_rule.eks_to_efs` | 삭제됨 | 제거됨 |
| 🟡 DESTROYED | `aws_security_group_rule.eks_to_redis` | 삭제됨 | 제거됨 |
| ✅ SURVIVED | `aws_eks_cluster.main` (bedrock-claude-eks) | ACTIVE | 유지됨 |
| ❌ FAILED | `aws_eks_cluster.main` 삭제 | 409 — nodegroups attached | — |
| ❌ FAILED | `aws_ecr_repository.bedrock_ag` 생성 | 이미 존재 | — |
| ❌ FAILED | `aws_efs_mount_target.eks_private[0,1]` 생성 | 이미 존재 | — |

---

## 영향 분석

### 1. Redis 삭제 (`bedrock-claude-redis`) — 🔴 CRITICAL
- auth-gateway가 세션 저장소로 Redis 사용 (`REDIS_URL` env)
- 삭제로 인해 모든 사용자 세션 무효화 → 재로그인 불가
- 대기 중인 편집 콜백 데이터 손실

### 2. OIDC Provider 삭제 — 🔴 CRITICAL  
- EKS 클러스터의 IRSA(IAM Roles for Service Accounts) 전체 중단
- 영향받는 ServiceAccount:
  - `claude-sessions/claude-terminal-sa` → Bedrock InvokeModel 불가
  - `platform/platform-admin-sa` → Bedrock InvokeModel 불가 (Lane B 목적 포함)
  - `kube-system/cluster-autoscaler-sa` → 오토스케일링 불가
- OIDC provider ARN이 변경되면 모든 IAM Role의 trust policy 재설정 필요

### 3. SG Rules 삭제 — 🟡 HIGH
- `eks_to_efs`: EKS 노드 → EFS NFS 트래픽 차단 → PVC 마운트 실패 가능
- `eks_to_redis`: EKS → ElastiCache 트래픽 차단 (Redis 삭제됐으므로 현재는 무의미)

### 4. Lane B IAM 변경 미적용
- `aws_iam_role.auth_gateway_bedrock`: AWS에 기존 존재, terraform state에 없음
- `aws_iam_role_policy.auth_gateway_bedrock_invoke`: **미생성** (apply 실패로 중단)
- `bedrock:Converse`, `bedrock:ConverseStream` 정책 **미반영**

---

## EKS 클러스터 생존 확인

```
aws eks describe-cluster --name bedrock-claude-eks:
  status: ACTIVE
  OIDC issuer: https://oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF
```

→ 클러스터 자체는 살아있음. OIDC issuer URL 동일 (재생성 시 동일 ARN 패턴 사용 가능)

---

## 복구 권장 순서

### 즉시 (now)

**Step 1: 충돌 리소스 terraform import**

```bash
cd infra/terraform

# 이미 AWS에 존재하는 ECR repo import
terraform import aws_ecr_repository.bedrock_ag bedrock-claude/bedrock-access-gateway

# 이미 AWS에 존재하는 EFS mount target import
# mount target ID 먼저 확인:
EFS_ID=fs-0a2b5924041425002
aws efs describe-mount-targets --file-system-id $EFS_ID --region ap-northeast-2 \
  --query 'MountTargets[].{id:MountTargetId,az:AvailabilityZoneName}' --output table

# 결과에서 AZ별 mount target ID로 import:
terraform import 'aws_efs_mount_target.eks_private[0]' <subnet-a-mount-target-id>
terraform import 'aws_efs_mount_target.eks_private[1]' <subnet-c-mount-target-id>

# auth_gateway_bedrock role import (이미 AWS에 존재)
terraform import aws_iam_role.auth_gateway_bedrock bedrock-claude-auth-gateway-bedrock
```

**Step 2: terraform apply 재실행**
- import 후 apply 시 OIDC provider 재생성 (기존 EKS cluster 사용)
- Redis 재생성 (~5분 ElastiCache 프로비저닝)
- SG rules 재생성
- `auth_gateway_bedrock_invoke` policy 생성 (Lane B 목적)

**Step 3: OIDC Provider ARN 확인 후 IRSA trust policy 유효성 검증**
- OIDC provider ARN이 기존과 동일한지 확인
- 다를 경우 모든 IAM Role의 trust policy 업데이트 필요

---

## terraform apply 원본 출력 (요약)

```
Destroyed:
  - aws_security_group_rule.eks_to_efs       (0s)
  - aws_iam_openid_connect_provider.eks      (1s)
  - aws_security_group_rule.eks_to_redis     (1s)
  - aws_elasticache_cluster.redis            (3m52s)

Errors:
  - EKS DeleteCluster 409: Cluster has nodegroups attached
  - ECR CreateRepository 400: RepositoryAlreadyExistsException
  - EFS CreateMountTarget 409: MountTargetConflict (x2)
```

---

## kubectl apply 실행 여부
**미실행** — terraform 실패 확인 후 중단. 사용자 지시 대기.
