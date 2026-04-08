# 1-Node-1-Pod Architecture Migration

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** m5.large 공유 노드(3 pod/node)에서 t3.medium 전용 노드(1 pod/node)로 전환하여 Pod 간 리소스 간섭을 원천 제거한다.

**Architecture:** 사용자별 t3.medium 노드 1대를 할당하고 Pod Anti-Affinity로 1:1을 강제한다. 기존 m5.large 노드그룹은 유지하되 신규 t3.medium 노드그룹을 추가하여 blue-green 전환한다. overprovisioning은 Anti-Affinity 추가 + replicas 조정. PDB로 voluntary eviction 차단.

**Tech Stack:** Terraform (EKS node group), Kubernetes (Anti-Affinity, PDB), Python (k8s_service.py, infra_policy.py)

**Safety:** N1103906 Pod (ip-10-0-10-20, presenter 노드)는 절대 방해하지 않는다. presenter 노드그룹과 시스템 노드는 변경하지 않는다.

---

## Task 1: Terraform — t3.medium 노드그룹 추가

**Files:**
- Modify: `infra/terraform/variables.tf:70-92`
- Modify: `infra/terraform/eks.tf:185-220`
- Modify: `infra/terraform/terraform.tfvars:28-31`

**Step 1: variables.tf에 t3.medium 노드그룹 변수 추가**

`eks_node_max_size` 아래(92번줄 뒤)에 추가:

```hcl
# ----- 1:1 전용 노드그룹 (t3.medium, 사용자별 1 node) -----

variable "eks_dedicated_node_instance_types" {
  description = "1:1 전용 노드 인스턴스 타입"
  type        = list(string)
  default     = ["t3.medium"]
}

variable "eks_dedicated_node_desired_size" {
  description = "1:1 전용 노드 희망 개수"
  type        = number
  default     = 2
}

variable "eks_dedicated_node_min_size" {
  description = "1:1 전용 노드 최소 개수 (0 = 야간 완전 축소 가능)"
  type        = number
  default     = 0
}

variable "eks_dedicated_node_max_size" {
  description = "1:1 전용 노드 최대 개수"
  type        = number
  default     = 15
}
```

**Step 2: eks.tf에 t3.medium 노드그룹 리소스 추가**

기존 `aws_eks_node_group.main` 블록(220번줄) 뒤에 추가:

```hcl
# ----- 1:1 전용 Node Group (t3.medium) -----
# 사용자별 노드 1대 전용 할당 — Pod 간 리소스 간섭 원천 제거

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

  tags = {
    "k8s.io/cluster-autoscaler/enabled"                       = "true"
    "k8s.io/cluster-autoscaler/${var.project_name}-eks"       = "owned"
    Owner   = "N1102359"
    Env     = "prod"
    Service = "sko-claude-ai-agent"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry,
  ]
}
```

**Step 3: terraform.tfvars에 값 추가**

파일 끝에 추가:

```hcl
# ----- 1:1 전용 노드그룹 (t3.medium) -----
eks_dedicated_node_instance_types = ["t3.medium"]
eks_dedicated_node_desired_size   = 2
eks_dedicated_node_min_size       = 0
eks_dedicated_node_max_size       = 15
```

**Step 4: terraform plan으로 검증**

```bash
cd infra/terraform && terraform plan
```

Expected: `Plan: 1 to add, 0 to change, 0 to destroy` (새 노드그룹 1개 추가만)

**Step 5: terraform apply**

```bash
terraform apply -auto-approve
```

Expected: 노드그룹 생성 완료 (3-5분). 기존 m5.large 노드그룹은 변경 없음.

**Step 6: 새 노드 확인**

```bash
kubectl get nodes -l role=claude-dedicated
```

Expected: t3.medium 노드 2대 Ready 상태.

**Step 7: Commit**

```bash
git add infra/terraform/variables.tf infra/terraform/eks.tf infra/terraform/terraform.tfvars
git commit -m "infra: add t3.medium dedicated node group for 1-node-1-pod"
```

---

## Task 2: infra_policy.py — dedicated 템플릿 추가

**Files:**
- Modify: `auth-gateway/app/models/infra_policy.py`

**Step 1: INFRA_TEMPLATES에 dedicated 템플릿 추가**

`"shared-large"` 블록(49번줄) 뒤에 추가:

```python
    "dedicated": {
        "nodegroup": "bedrock-claude-dedicated-nodes",
        "node_selector": {"role": "claude-dedicated"},
        "max_pods_per_node": 1,
        "cpu_request": "500m",
        "cpu_limit": "1000m",
        "memory_request": "1.5Gi",
        "memory_limit": "3Gi",
        "shared_dir_writable": False,
    },
```

**Step 2: INFRA_TEMPLATE_DESCRIPTIONS에 설명 추가**

```python
    "dedicated": "전용 (t3.medium, 노드당 1명, 리소스 격리)",
```

**Step 3: standard 템플릿의 기본값을 dedicated로 전환**

`k8s_service.py:188`에서 `INFRA_TEMPLATES["standard"]`를 fallback으로 사용하므로, 기존 사용자들이 자동으로 dedicated를 사용하도록 하려면 sessions.py에서 infra_policy를 dedicated로 전달해야 한다. 이것은 Task 4에서 처리.

**Step 4: Commit**

```bash
git add auth-gateway/app/models/infra_policy.py
git commit -m "feat: add dedicated infra template for 1-node-1-pod"
```

---

## Task 3: k8s_service.py — Pod Anti-Affinity 추가

**Files:**
- Modify: `auth-gateway/app/services/k8s_service.py:215` (V1PodSpec 내부)

**Step 1: create_pod()의 V1PodSpec에 affinity 파라미터 추가**

`k8s_service.py`의 `V1PodSpec(` 블록(215번줄) 내부, `tolerations=` 뒤(264번줄)에 추가:

```python
                # 1-node-1-pod 격리: 같은 노드에 claude-terminal Pod 중복 배치 방지
                affinity=client.V1Affinity(
                    pod_anti_affinity=client.V1PodAntiAffinity(
                        required_during_scheduling_ignored_during_execution=[
                            client.V1PodAffinityTerm(
                                label_selector=client.V1LabelSelector(
                                    match_labels={"app": "claude-terminal"},
                                ),
                                topology_key="kubernetes.io/hostname",
                            ),
                        ],
                    ),
                ) if infra.get("max_pods_per_node", 3) == 1 else None,
```

조건부 적용: `max_pods_per_node == 1`인 템플릿(dedicated, premium)에서만 Anti-Affinity 활성화. standard(3) / shared-large(2)는 기존 동작 유지.

**Step 2: Commit**

```bash
git add auth-gateway/app/services/k8s_service.py
git commit -m "feat: add pod anti-affinity for 1-node-1-pod templates"
```

---

## Task 4: sessions.py — 기본 템플릿을 dedicated로 전환

**Files:**
- Modify: `auth-gateway/app/routers/sessions.py`

**Step 1: _ensure_node_capacity()의 노드 필터링 로직 업데이트**

`sessions.py:109-115`에서 일반 사용자 노드를 찾는 로직에 `claude-dedicated` 역할 추가:

```python
            # 일반 사용자: role=claude-terminal 또는 role=claude-dedicated 노드
            all_nodes = v1.list_node().items
            nodes = [
                n for n in all_nodes
                if n.metadata.labels.get("role") in ("claude-terminal", "claude-dedicated")
            ]
```

**Step 2: 세션 생성 시 기본 infra_policy를 dedicated로 변경**

세션 생성 엔드포인트에서 `infra_policy`가 None일 때의 fallback을 dedicated로 변경. 
`k8s_service.py:188`의 fallback은 `INFRA_TEMPLATES["standard"]`이므로, sessions.py에서 명시적으로 dedicated를 전달:

sessions.py의 세션 생성 호출부에서 infra_policy 파라미터에 `INFRA_TEMPLATES["dedicated"]`를 전달하도록 수정.

(정확한 위치는 sessions.py의 `create_session` 엔드포인트에서 `k8s.create_pod()` 호출부)

**Step 3: Commit**

```bash
git add auth-gateway/app/routers/sessions.py
git commit -m "feat: default to dedicated template for new sessions"
```

---

## Task 5: overprovisioning.yaml — Anti-Affinity 추가 + replicas 조정

**Files:**
- Modify: `infra/k8s/autoscaling/overprovisioning.yaml`

**Step 1: overprovisioning Deployment에 Anti-Affinity 추가**

overprovisioning Pod끼리도 같은 노드에 배치되지 않도록, 그리고 claude-terminal Pod과도 같은 노드에 배치되지 않도록 설정. `spec.template.spec`에 추가:

```yaml
      affinity:
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            # overprovisioning Pod끼리 같은 노드 배치 금지
            - labelSelector:
                matchLabels:
                  app: overprovisioning
              topologyKey: "kubernetes.io/hostname"
            # 사용자 Pod이 있는 노드에도 배치 금지
            - labelSelector:
                matchLabels:
                  app: claude-terminal
              topologyKey: "kubernetes.io/hostname"
```

**Step 2: nodeSelector를 claude-dedicated로 변경**

```yaml
      nodeSelector:
        role: claude-dedicated    # claude-terminal → claude-dedicated
```

**Step 3: toleration 업데이트 (필요 시)**

새 노드그룹에 taint가 없으면 toleration 제거 가능. t3.medium dedicated 노드에는 별도 taint를 걸지 않으므로 기존 toleration은 유지해도 무방.

**Step 4: replicas는 2 유지**

overprovisioning 2개 = 노드 2대 사전 확보. 1:1 모델에서 동시 로그인 2명까지 즉시 수용.

**Step 5: kubectl apply**

```bash
kubectl apply -f infra/k8s/autoscaling/overprovisioning.yaml
```

**Step 6: 확인**

```bash
kubectl get pods -n claude-sessions -l app=overprovisioning -o wide
```

Expected: overprovisioning Pod 2개가 각각 다른 claude-dedicated 노드에 배치.

**Step 7: Commit**

```bash
git add infra/k8s/autoscaling/overprovisioning.yaml
git commit -m "feat: overprovisioning targets dedicated nodes with anti-affinity"
```

---

## Task 6: PodDisruptionBudget 추가

**Files:**
- Create: `infra/k8s/pdb.yaml`

**Step 1: PDB 매니페스트 생성**

```yaml
# PodDisruptionBudget — 사용자 터미널 Pod의 voluntary eviction 차단
# kubectl drain, 노드 업데이트, Autoscaler voluntary scale-down 시
# claude-terminal Pod이 퇴거되지 않도록 보호
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: claude-terminal-pdb
  namespace: claude-sessions
spec:
  maxUnavailable: 0
  selector:
    matchLabels:
      app: claude-terminal
```

**Step 2: kubectl apply**

```bash
kubectl apply -f infra/k8s/pdb.yaml
```

**Step 3: 확인**

```bash
kubectl get pdb -n claude-sessions
```

Expected: `claude-terminal-pdb` with `ALLOWED DISRUPTIONS: 0`

**Step 4: Commit**

```bash
git add infra/k8s/pdb.yaml
git commit -m "feat: add PDB to prevent voluntary eviction of user pods"
```

---

## Task 7: idle_cleanup_service.py — webapp 규칙 제거 (이미 완료)

이번 세션 초반에 이미 완료됨. webapp (port 3000) keep-alive 규칙 제거, 파일 활동 기반 유지만 남김.

---

## Task 8: 기존 m5.large 노드그룹 축소

**Safety: N1103906 Pod가 presenter 노드(ip-10-0-10-20)에서 실행 중이므로 presenter 노드그룹은 절대 변경하지 않는다. m5.large user 노드그룹(bedrock-claude-nodes)만 축소.**

**Step 1: 현재 m5.large user 노드에 사용자 Pod이 없는지 확인**

```bash
kubectl get pods -n claude-sessions -l app=claude-terminal -o wide
```

N1103906은 presenter 노드(ip-10-0-10-20)에 있으므로, m5.large claude-terminal 노드(ip-10-0-10-79, ip-10-0-20-57, ip-10-0-20-74)에는 Pod이 없어야 함 (Task 이전에 10개 삭제 완료).

**Step 2: terraform.tfvars에서 기존 노드그룹 축소**

```hcl
eks_node_desired_size   = 0
eks_node_min_size       = 0
eks_node_max_size       = 0
```

**Step 3: terraform apply**

```bash
cd infra/terraform && terraform apply -auto-approve
```

Expected: 기존 m5.large 노드 3대 종료. N1103906은 presenter 노드에 있으므로 영향 없음.

**Step 4: Commit**

```bash
git add infra/terraform/terraform.tfvars
git commit -m "infra: scale down legacy m5.large node group to zero"
```

---

## Task 9: 검증 — 신규 Pod 생성 테스트

**Step 1: 테스트 Pod 생성**

관리자 API 또는 웹 로그인으로 테스트 사용자 세션 생성. dedicated 템플릿이 적용되는지 확인.

**Step 2: 확인 사항**

```bash
# Pod이 claude-dedicated 노드에 배치되었는지
kubectl get pod claude-terminal-test001 -n claude-sessions -o wide

# Anti-Affinity가 적용되었는지
kubectl get pod claude-terminal-test001 -n claude-sessions -o yaml | grep -A 10 antiAffinity

# PDB 상태
kubectl get pdb -n claude-sessions

# 노드 상태
kubectl get nodes -l role=claude-dedicated
```

**Step 3: 두 번째 테스트 Pod 생성 — Anti-Affinity 검증**

두 번째 사용자로 로그인 → 반드시 다른 노드에 배치되어야 함.

**Step 4: 테스트 Pod 정리**

```bash
kubectl delete pod claude-terminal-test001 -n claude-sessions
```

---

## Execution Order & Safety

```
Task 1 (Terraform)     → 새 노드그룹 생성 (기존 인프라 무변경)
Task 2 (infra_policy)  → 코드 변경 (배포 전까지 미적용)
Task 3 (k8s_service)   → 코드 변경 (배포 전까지 미적용)
Task 4 (sessions)      → 코드 변경 (배포 전까지 미적용)
Task 5 (overprovisioning) → kubectl apply (즉시 적용)
Task 6 (PDB)           → kubectl apply (즉시 적용, N1103906도 보호)
Task 7 (idle cleanup)  → 이미 완료
Task 8 (m5.large 축소) → N1103906 안전 확인 후 실행
Task 9 (검증)          → auth-gateway 배포 후 테스트
```

**N1103906 보호 원칙:**
- presenter 노드그룹 변경 없음
- PDB가 Task 6에서 먼저 적용되어 voluntary eviction 차단
- m5.large 축소(Task 8)는 user 노드만 대상, presenter 노드 무관
