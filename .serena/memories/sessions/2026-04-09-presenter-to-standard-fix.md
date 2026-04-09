# Session: Presenter Node → Standard Policy Fix (2026-04-09)

## Summary
정병오(N1001063), 김광우(N1001064) 두 사용자의 infra_policy를 presenter-node → standard(NULL)로 변경.

## Problem
- 정병오 Pod(`claude-terminal-n1001063`)이 Pending 상태로 106분 체류
- 원인: `nodeSelector: role=presenter` → presenter-node nodegroup(m5.large, desiredSize=0)
- Cluster autoscaler scale-up 실패: Pod CPU request(1800m) > m5.large 가용 CPU(1730m, DaemonSet 200m 차감 후)
- DaemonSet overhead: kube-proxy 100m + fluent-bit 50m + aws-node 50m = 200m

## Resolution
1. DB `users.infra_policy` → NULL (standard template fallback) for N1001063, N1001064
2. Stuck pod `claude-terminal-n1001063` deleted
3. 재로그인 시 `role=claude-dedicated` 노드에 정상 배치

## Key Insight
presenter-node(m5.large, 2vCPU)은 allocatable 1930m에서 DaemonSet 200m을 빼면 1730m만 가용.
1800m CPU request는 수용 불가. presenter-node를 사용하려면 m5.xlarge(4vCPU) 이상 필요.
