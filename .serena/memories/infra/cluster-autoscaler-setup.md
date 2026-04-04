# Cluster Autoscaler Configuration (2026-04-03)

## Architecture
- IRSA-based authentication: `bedrock-claude-cluster-autoscaler` IAM Role → `kube-system:cluster-autoscaler` SA
- Pinned to system nodes: `nodeSelector: role=system` + `toleration: dedicated=system:NoSchedule`
- ASG auto-discovery via tags: `k8s.io/cluster-autoscaler/enabled=true`, `k8s.io/cluster-autoscaler/bedrock-claude-eks=owned`

## Scale-Down Settings
- `--scale-down-unneeded-time=10m`
- `--scale-down-delay-after-add=10m`
- `--scale-down-delay-after-delete=2m`
- `--expander=least-waste`
- User nodegroup min_size=0 (allows full scale-to-zero)

## Nighttime Policy
- 18:30 KST (Mon-Fri): CronJob `overprov-stop` → overprovisioning replicas=0
- 10min later: Cluster Autoscaler removes empty user nodes → 0 nodes
- 08:40 KST (Mon-Fri): CronJob `overprov-start` → overprovisioning replicas=2
- ~80s later: new user node Ready with dummy pods

## Key Files
- `infra/k8s/autoscaling/cluster-autoscaler.yaml` — Deployment + RBAC
- `infra/k8s/autoscaling/overprovisioning.yaml` — Dummy pods with safe-to-evict
- `infra/terraform/eks.tf` — IRSA role + ASG tags + node group config

## Gotchas
- CronJobs must use `serviceAccountName: nighttime-scaler` (not default)
- Autoscaler MUST run on system node, otherwise blocks user node scale-down
- EKS nodegroup min_size change requires direct `aws eks update-nodegroup-config` (Terraform may skip if no other changes)
