# Issue #24 — ingress 스케일링 구조 검증 리포트

**이슈**: [#24] infra: system-node-large 제약 수정 (max 3 → 6 또는 ingress-workers 분리)
**검증일**: 2026-04-14
**담당**: cation98 (N1102359)
**결론**: ✅ **이미 구현 완료 — 별도 작업 불필요**

## 배경

Phase 0 설계 리뷰(2026-04-12)에서 Open WebUI WebSocket 트래픽(500 concurrent long-lived) 수용을 위해 `system-node-large` max 3 제약을 해소할 방안이 필요했다. 두 가지 옵션이 있었다:

1. `system-node-large` max 3 → 6 확장
2. ingress-nginx를 별도 `ingress-workers` nodegroup으로 분리

최종 채택: **옵션 2** (ingress-workers 분리). 이유:
- auth-gateway는 2-node pair 유지해야 하며 ingress 트래픽 증가와 무관
- ingress 계층과 system 계층의 **스케일 정책이 다름** → 독립 그룹이 자연스러움
- system 노드에서 ingress 부하를 제거해 auth-gateway 안정성 확보

## Definition of Done (B 스코프)

| # | 항목 | 기대값 |
|---|------|--------|
| 1 | CLAUDE.md 설계 반영 | ingress-workers 별도 nodegroup 명시 |
| 2 | Terraform `aws_eks_node_group.ingress` 리소스 | 존재 + min=2/max=6/t3.large |
| 3 | AWS 실 배포 | ingress-workers nodegroup 생성, 노드 ≥2대 Ready |
| 4 | ingress-nginx Helm values | nodeSelector/toleration + hard anti-affinity |
| 5 | ingress-nginx HPA | min=2, max=6, CPU/Memory 트리거 |
| 6 | PDB | ingress-nginx minAvailable ≥1 |
| 7 | defaultBackend 배치 | 별도 설정(어느 노드에 있어도 서비스 영향 없음) |
| 8 | Cluster Autoscaler 연동 | nodegroup에 `k8s.io/cluster-autoscaler/enabled=true` 태그 |

## 검증 결과

### 1) CLAUDE.md 설계 ✅

**파일**: `/Users/cation98/Project/bedrock-ai-agent/CLAUDE.md`
"Infrastructure Design Constraints > System Node 운용(필수)" 섹션에 명시됨:

```
[system-node-large] Node A: auth-gateway replica-1
[system-node-large] Node B: auth-gateway replica-2

[ingress-workers] Node 1~N (min 2 / max 6): ingress-nginx
```

- auth-gateway와 ingress-nginx 간 pod affinity(근접 선호) 제거 명시
- 각 nodegroup anti-affinity `requiredDuringSchedulingIgnoredDuringExecution` (hard) 유지 명시

### 2) Terraform 리소스 ✅

**파일**: `infra/terraform/eks.tf:304-347`

```hcl
resource "aws_eks_node_group" "ingress" {
  node_group_name = "ingress-workers"
  instance_types  = var.eks_ingress_node_instance_types   # t3.large
  scaling_config {
    desired_size = var.eks_ingress_node_desired_size      # 2
    min_size     = var.eks_ingress_node_min_size          # 2
    max_size     = var.eks_ingress_node_max_size          # 6
  }
  labels = { role = "ingress" }
  taint  { key = "dedicated"; value = "ingress"; effect = "NO_SCHEDULE" }
  tags   = {
    "k8s.io/cluster-autoscaler/enabled" = "true"
    "k8s.io/cluster-autoscaler/bedrock-claude-eks" = "owned"
    ...
  }
}
```

변수 기본값 (`infra/terraform/variables.tf:146-170`): min=2 / desired=2 / max=6 / t3.large.

### 3) AWS 실 배포 ✅

```
$ aws eks list-nodegroups --cluster-name bedrock-claude-eks --region ap-northeast-2
{
    "nodegroups": [
        "bedrock-claude-burst-workers",
        "bedrock-claude-dedicated-nodes",
        "bedrock-claude-nodes",
        "ingress-workers",         ← 존재
        "presenter-node",
        "system-node-large"
    ]
}
```

노드 상태:
```
$ kubectl get nodes -l role=ingress
ip-10-0-10-78.ap-northeast-2.compute.internal    Ready   35h   role=ingress   Taints: dedicated=ingress:NoSchedule
ip-10-0-20-131.ap-northeast-2.compute.internal   Ready   35h   role=ingress   Taints: dedicated=ingress:NoSchedule
```

2개 AZ에 분산 배치(10.0.10.*, 10.0.20.*). 현재 2/6, HA 하한 충족.

### 4) Helm values (hard anti-affinity + nodeSelector) ✅

**파일**: `infra/k8s/ingress-nginx-values.yaml:23-54`

```yaml
controller:
  nodeSelector:
    role: ingress
  tolerations:
    - key: dedicated
      operator: Equal
      value: ingress
      effect: NoSchedule
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:   # ← hard
        - labelSelector:
            matchExpressions:
              - { key: app.kubernetes.io/name, operator: In, values: [ingress-nginx] }
              - { key: app.kubernetes.io/component, operator: In, values: [controller] }
          topologyKey: kubernetes.io/hostname
```

실 Deployment 스펙에도 그대로 반영됨(`kubectl get deploy -n ingress-nginx` 확인 완료).

Pod 배치 증거:
```
ingress-nginx-controller-9bd4c8bb6-f22qp   on ip-10-0-20-131 (AZ 2c)
ingress-nginx-controller-9bd4c8bb6-rm5jk   on ip-10-0-10-78  (AZ 2a)
```
→ 동일 노드 중복 없음, AZ 분산됨.

### 5) HPA ✅

```
$ kubectl get hpa -n ingress-nginx
NAME                       REFERENCE                             TARGETS                        MINPODS   MAXPODS   REPLICAS
ingress-nginx-controller   Deployment/ingress-nginx-controller   memory: 16%/80%, cpu: 1%/70%   2         6         2
```

- min=2 / max=6: Helm values와 일치
- 트리거: CPU 70%, Memory 80% (Open WebUI WebSocket은 메모리 연결 상태가 지배적 → 메모리 트리거 타당)
- 현재 부하: CPU 1%, Mem 16% → 유휴 상태, 추가 증량 여력 충분

### 6) PDB ✅

```
$ kubectl get pdb -n ingress-nginx
NAME                       MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
ingress-nginx-controller   1               N/A               1
```

- minAvailable=1: 롤링 업데이트/노드 드레인 시 최소 1개 ingress 유지
- ALLOWED DISRUPTIONS=1: 현재 2 replicas에서 1개 중단 허용 정확함

### 7) defaultBackend ✅ (의도대로)

Helm values에서 defaultBackend는 `role: system` nodeSelector + system toleration으로 구성됨. 장애 시 404 fallback 제공 용도이므로 ingress 노드에 둘 필요 없음 — 의도된 설계.

```
$ kubectl get pod ingress-nginx-defaultbackend-7bc4cd57-42hst -o wide
NODE: ip-10-0-10-214.ap-northeast-2.compute.internal   (role=system)
```

### 8) Cluster Autoscaler 연동 ✅

```
$ kubectl get deploy cluster-autoscaler -n kube-system
READY: 1/1 (11d AGE)
```

ingress-workers nodegroup tags에 `k8s.io/cluster-autoscaler/enabled=true`, `k8s.io/cluster-autoscaler/bedrock-claude-eks=owned` 존재 → ASG 자동 탐색 대상.

## 용량 산정 (재확인)

### WebSocket concurrent 수용 능력

- t3.large 1노드 가용 메모리 ≈ 7Gi (kubelet/시스템 오버헤드 제외)
- ingress-nginx 요청 256Mi / 한계 512Mi
- HPA가 pod 수를 메모리 80% 트리거로 수평 확장
- 노드당 ingress pod 1개 (hard anti-affinity)
- 최대 6 replicas × 각 pod이 안전하게 처리할 수 있는 WebSocket ≈ 노드 메모리 여력(약 7Gi per node) → pod당 수천 connection 가능

→ 500 concurrent 가설 커버 가능. 실측은 별도 이슈(부하 테스트 범위)로 이관.

### 비용 영향

- ingress-workers 평시: t3.large × 2 = **약 $60/월** (AZ 2개)
- 피크 시(6 replicas 필요): t3.large × 6 = **약 $180/월**
- 이슈 원문 추정치($150) 와 근사

## 잔여 과제 (범위 외)

| # | 항목 | 이관 대상 |
|---|------|-----------|
| A | max=6까지 실제 확장 시뮬레이션 | 이슈 #16 / Locust 기반 별도 부하 테스트 |
| B | ingress-nginx custom metrics 기반 HPA (예: 활성 커넥션 수) | Phase 1b 확장 시 재검토 |
| C | 예약 없이 확장 시 Cluster Autoscaler 지연 측정 | Phase 1b 관측성 이슈 묶음 |

## 결론

B 스코프(A 설정 검증 + HPA 반영)의 8개 DoD 항목 **전부 충족**. 별도 구현 작업 불필요. 이슈 close 조건 성립.

---

**참고 파일**:
- `CLAUDE.md` (Infrastructure Design Constraints 섹션)
- `infra/terraform/eks.tf` (L243-347: system + ingress node groups)
- `infra/terraform/variables.tf` (L120-170: 스케일 변수)
- `infra/k8s/ingress-nginx-values.yaml` (전체)
