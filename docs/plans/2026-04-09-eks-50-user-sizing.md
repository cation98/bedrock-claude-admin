# EKS 50-User Sizing Plan

> Date: 2026-04-09  
> Phase: 2 (Team Lead Workshop, 50 users simultaneous)  
> Related: `infra/terraform/eks.tf`, `auth-gateway/app/models/infra_policy.py`

---

## 1. Current Configuration (Phase 1)

### Node Groups

| Node Group | Instance Type | Min | Max | Purpose |
|------------|--------------|-----|-----|---------|
| `bedrock-claude-nodes` (main) | m5.large | 2 | 4 | System workloads, shared services |
| `bedrock-claude-dedicated-nodes` | t3.medium | 0 | 15 | 1:1 user Pod isolation |
| `presenter-node` | m5.xlarge | - | - | Presenter (enterprise template) |

### t3.medium Allocatable Resources

| Resource | Node Total | Allocatable | DaemonSet Overhead | Available for Pod |
|----------|-----------|-------------|-------------------|-------------------|
| CPU | 2000m | 1930m | ~230m (aws-node, kube-proxy, ebs-csi) | ~1700m |
| Memory | 4096Mi | ~3297Mi | ~397Mi | ~2900Mi |

### Current Pod Template ("standard")

```python
"standard": {
    "nodegroup": "bedrock-claude-dedicated-nodes",
    "max_pods_per_node": 1,
    "cpu_request": "1700m",
    "cpu_limit": "1700m",
    "memory_request": "2900Mi",
    "memory_limit": "2900Mi",
}
```

### Current Overprovisioning

- Replicas: **0** (disabled in normal operation)
- Workshop mode: manually set to 1-3 to pre-warm nodes
- Resource request per dummy Pod: 1700m CPU, 2900Mi memory (identical to user Pod)

---

## 2. 50-User Scenario: Option A (1-node-1-pod, t3.medium)

**Architecture: Maintain current 1:1 isolation model.**

### Resource Calculation

| Item | Value |
|------|-------|
| Users simultaneous | 50 |
| Pods needed | 50 |
| Nodes per Pod | 1 (1:1 isolation) |
| **Total dedicated nodes** | **50 x t3.medium** |
| System node group (m5.large) | 2 (unchanged) |
| **Total nodes** | **52** |

### Terraform Changes Required

```hcl
# variables.tf
variable "eks_dedicated_node_max_size" {
  default = 55  # 50 users + 5 buffer for rolling updates
}
```

### Cost Estimate (ap-northeast-2, On-Demand)

| Component | Unit Cost | Count | Hourly | Monthly (730h) |
|-----------|----------|-------|--------|----------------|
| t3.medium (dedicated) | $0.052/hr | 50 | $2.60 | $1,898 |
| m5.large (system) | $0.118/hr | 2 | $0.236 | $172 |
| EBS gp3 30GB | $0.096/GB/mo | 50 | - | $144 |
| **Total** | | | **$2.84/hr** | **$2,214/mo** |

> Note: Workshop events are typically 2-4 hours. If 50 nodes run for 4 hours only:  
> One-time cost = 50 x $0.052 x 4 = **$10.40 per workshop session**

### Autoscaler Configuration

```yaml
# cluster-autoscaler adjustments
- --scale-down-delay-after-add=10m     # Keep (prevents premature scale-down)
- --scale-down-unneeded-time=10m       # Reduce to 5m for faster cleanup
```

### Workshop Mode Overprovisioning

For 50 users logging in within ~10 minutes, pre-warm 5-10 nodes:

```yaml
# overprovisioning.yaml
spec:
  replicas: 5  # Pre-warm 5 nodes (workshop mode)
```

Remaining 45 nodes scale up in batches as users log in. Expected wait time:
- First 5 users: immediate (pre-warmed)
- Remaining 45 users: ~2-3 min per batch of ~10 nodes (Cluster Autoscaler + EC2 launch)

### Pros
- **Complete resource isolation**: No CPU/memory contention between users
- **Simple mental model**: 1 user = 1 node, easy to debug and monitor
- **Consistent performance**: Each user gets full t3.medium capacity
- **Existing architecture**: No code changes to k8s_service.py or infra_policy.py

### Cons
- **Higher cost at scale**: $2,214/mo at sustained 50-node usage
- **EC2 limits**: May need to request EC2 service limit increase for t3.medium in ap-northeast-2 (default limit varies by account)
- **Subnet sizing**: Current 2 subnets (/24 each = 502 IPs total) are sufficient for 50 nodes + ENIs

---

## 3. 50-User Scenario: Option B (2-pods-per-node, t3.large)

**Architecture: Place 2 user Pods per t3.large node.**

### t3.large Allocatable Resources

| Resource | Node Total | Allocatable | DaemonSet Overhead | Available for Pods |
|----------|-----------|-------------|-------------------|-------------------|
| CPU | 2000m (2 vCPU) | 1930m | ~230m | ~1700m |
| Memory | 8192Mi | ~7440Mi | ~397Mi | ~7043Mi |

> t3.large has the same 2 vCPUs as t3.medium but **double the memory** (8GB vs 4GB).

### Per-Pod Resource Allocation (Reduced)

```python
"standard_dense": {
    "nodegroup": "bedrock-claude-dense-nodes",
    "max_pods_per_node": 2,
    "cpu_request": "800m",    # 1700m / 2 = 850m, rounded down
    "cpu_limit": "850m",
    "memory_request": "3200Mi",  # 7043Mi / 2 = ~3500Mi, with margin
    "memory_limit": "3400Mi",
}
```

### Resource Calculation

| Item | Value |
|------|-------|
| Users simultaneous | 50 |
| Pods needed | 50 |
| Pods per node | 2 |
| **Total dedicated nodes** | **25 x t3.large** |
| System node group (m5.large) | 2 (unchanged) |
| **Total nodes** | **27** |

### Cost Estimate (ap-northeast-2, On-Demand)

| Component | Unit Cost | Count | Hourly | Monthly (730h) |
|-----------|----------|-------|--------|----------------|
| t3.large (dedicated) | $0.104/hr | 25 | $2.60 | $1,898 |
| m5.large (system) | $0.118/hr | 2 | $0.236 | $172 |
| EBS gp3 30GB | $0.096/GB/mo | 25 | - | $72 |
| **Total** | | | **$2.84/hr** | **$2,142/mo** |

### Pros
- **Fewer nodes**: 25 vs 50, simpler cluster management
- **Slightly lower EBS cost**: Half the volumes
- **Faster scale-up**: Only need to launch 25 nodes instead of 50

### Cons
- **Breaks 1:1 isolation**: CPU contention between 2 users on the same node
- **CPU halved per user**: 800m vs 1700m -- Claude Code is CPU-intensive during `claude --dangerously-skip-permissions`, this will noticeably degrade performance
- **Code changes required**: New infra template, k8s_service.py Pod Anti-Affinity rework, overprovisioning resource adjustments
- **Noisy neighbor risk**: One user's heavy Claude session impacts the other user on the same node
- **Debugging complexity**: Issues become harder to isolate per-user

---

## 4. 50-User Scenario: Option C (Spot Instances, t3.medium 1:1)

**Architecture: Keep 1:1 isolation but use Spot Instances for cost savings.**

### Cost Estimate (ap-northeast-2, Spot ~60-70% discount)

| Component | Unit Cost | Count | Hourly | Monthly (730h) |
|-----------|----------|-------|--------|----------------|
| t3.medium Spot | ~$0.016/hr | 50 | $0.80 | $584 |
| m5.large On-Demand (system) | $0.118/hr | 2 | $0.236 | $172 |
| EBS gp3 30GB | $0.096/GB/mo | 50 | - | $144 |
| **Total** | | | **$1.04/hr** | **$900/mo** |

> Spot pricing fluctuates. The $0.016 estimate is based on typical ap-northeast-2 t3.medium spot history (~70% savings). Actual savings may vary.

### Terraform Changes

```hcl
resource "aws_eks_node_group" "dedicated_spot" {
  # ... same as dedicated but with:
  capacity_type  = "SPOT"
  instance_types = ["t3.medium", "t3a.medium"]  # Multiple types for Spot availability
}
```

### Pros
- **Major cost savings**: ~60-70% reduction vs On-Demand
- **Maintains 1:1 isolation**: No architectural changes needed
- **Workshop-friendly**: Short duration (2-4hr) makes interruption unlikely

### Cons
- **Interruption risk**: AWS can reclaim Spot instances with 2-min warning
- **Not suitable for long sessions**: Unacceptable for daily developer use
- **Workshop disruption**: If a node is reclaimed mid-session, user loses terminal
- **Mitigation needed**: Instance diversification, graceful shutdown handling

---

## 5. Comparison Summary

| Aspect | Option A: t3.medium 1:1 | Option B: t3.large 2:1 | Option C: Spot 1:1 |
|--------|------------------------|------------------------|---------------------|
| **Nodes needed** | 50 | 25 | 50 |
| **Monthly cost** | $2,214 | $2,142 | $900 |
| **Workshop cost (4hr)** | $10.40 | $10.40 | $3.20 |
| **Per-user CPU** | 1700m | 800m | 1700m |
| **Per-user Memory** | 2900Mi | 3200Mi | 2900Mi |
| **Isolation** | Full | Shared node | Full |
| **Code changes** | Minimal (max_size only) | Significant | Moderate |
| **Interruption risk** | None | None | High |
| **Performance consistency** | High | Medium | High (when available) |

---

## 6. Recommendation

### Primary: Option A (1-node-1-pod, t3.medium) with Workshop Mode

**Rationale:**

1. **Workshop use-case dominates**: Phase 2 is team-lead workshops (2-4 hour sessions), not sustained 24/7 usage. The per-workshop cost of ~$10 is negligible.

2. **Performance is critical for workshops**: Participants are evaluating the platform. Degraded performance from shared nodes (Option B) creates a poor first impression. The 800m CPU limit would noticeably slow Claude Code operations.

3. **No code changes needed**: Only `eks_dedicated_node_max_size` needs to increase from 15 to 55. The existing 1:1 architecture, Pod Anti-Affinity, autoscaler config, and infra_policy.py all work unchanged.

4. **Monthly cost is manageable**: Even at sustained usage, $2,214/mo is within corporate budget for an internal developer platform. In practice, workshop nodes spin down after 10 minutes idle, so sustained 50-node cost is unrealistic.

5. **Spot risk unacceptable for workshops**: Losing a terminal mid-presentation undermines trust in the platform.

### Required Changes for Option A

#### 1. Terraform (`infra/terraform/variables.tf`)

```hcl
variable "eks_dedicated_node_max_size" {
  description = "1:1 전용 노드 최대 개수"
  type        = number
  default     = 55   # 50 users + 5 buffer (was: 15)
}
```

#### 2. Workshop Pre-warming (`infra/k8s/autoscaling/overprovisioning.yaml`)

Before a workshop, set replicas to pre-warm nodes:

```bash
# 워크숍 30분 전 실행
kubectl scale deployment overprovisioning -n claude-sessions --replicas=5

# 워크숍 종료 후 복원
kubectl scale deployment overprovisioning -n claude-sessions --replicas=0
```

#### 3. EC2 Service Limits

Verify the AWS account has sufficient t3.medium vCPU quota in ap-northeast-2:

```bash
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-1216C47A \
  --region ap-northeast-2
```

50 t3.medium nodes = 100 vCPUs. Default On-Demand vCPU limit is typically 256, so this should be within limits, but verify.

#### 4. Subnet IP Capacity

Current: 2 private subnets x /24 = 2 x 251 usable IPs = 502 IPs total.  
Required: 50 nodes x ~3 ENIs each = ~150 IPs.  
Status: **Sufficient** (502 available >> 150 required).

### Future Consideration: Option C for Daily Use (Phase 3)

When transitioning to sustained daily use (10 developers, Phase 3), consider a **hybrid approach**:
- On-Demand t3.medium for primary nodes (reliable for all-day development)
- Spot instances as overflow capacity during peak hours
- This can be implemented later via a mixed Spot/On-Demand node group

---

## 7. Action Items

| # | Action | Priority | Owner |
|---|--------|----------|-------|
| 1 | Increase `eks_dedicated_node_max_size` from 15 to 55 | High | Terraform |
| 2 | Verify EC2 vCPU service quota (need >= 110 vCPUs) | High | AWS Console |
| 3 | Document workshop pre-warming procedure (overprovisioning replicas) | Medium | Runbook |
| 4 | Test autoscaler behavior with 10+ simultaneous node launches | Medium | Load test |
| 5 | Create workshop startup script (pre-warm + monitor) | Low | scripts/ |
| 6 | Evaluate Spot instances for Phase 3 daily developer use | Low | Future |
