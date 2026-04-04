# System Node Upgrade + Platform Fixes (2026-04-03)

## System Node Upgrade
- t3.medium → t3.large (memory 4GB → 8GB)
- Old nodegroup `system-node` deleted, new `system-node-large` created
- Tags: Owner=N1102359, Env=prod, Service=sko-claude-ai-agent

## Critical Fixes During Upgrade
- CoreDNS needed `dedicated=system` toleration (was Pending, broke all DNS)
- efs-csi-controller needed same toleration (blocked user node scale-down)
- auth-gateway SA changed to `platform-admin-sa` (IRSA) for AWS API access
- DB connection retry logic added (IRSA token mount delays DNS ~15s)

## Pod Distribution Policies
- auth-gateway: preferredDuringScheduling anti-affinity (spread across system nodes)
- overprovisioning: nodeSelector `role=claude-terminal` (prevent scheduling on presenter nodes)
- presenter nodes: `dedicated=presenter:NoSchedule` taint added to nodegroup
- presenter toleration added to user pod creation code (k8s_service.py)

## Prompt Audit
- Existing 2h collection was failing due to JSON parsing bug (Korean UTF-8)
- Fixed: ensure_ascii=False + extract both user+assistant messages
- New table: prompt_audit_conversations (full conversation history)
- Interval reduced to 30min

## Session Persistence
- `--continue` flag added to Claude CLI launch
- backup-chat now includes sessions/ dir for /resume support
- 30min periodic backup already existed (entrypoint.sh line 346)

## Pending Lunch Deploy
- auth-gateway image rebuild (prompt audit fix + IRSA SA)
- claude-code-terminal image rebuild (--continue + sessions backup)
