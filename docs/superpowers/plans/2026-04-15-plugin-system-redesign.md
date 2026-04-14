# Plugin System Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix broken plugin system in User Pod (enable superpowers/frontend-design/feature-dev) and deploy Gitea as internal git gateway for plugin mirror + code storage, with github.com egress blocked.

**Architecture:** Gitea on EKS serves dual role (plugin marketplace mirror + user code repos). User Pod pre-bakes 3 plugins with correct `plugins-config.json` enabled flags. NetworkPolicy blocks public GitHub; all git traffic flows through Gitea. Auth Gateway auto-provisions Gitea users on SSO login and injects tokens into Pod env.

**Tech Stack:** EKS, Helm (gitea-charts/gitea), RDS PostgreSQL, FastAPI (Python 3.12), Docker (node:22), NetworkPolicy, Terraform.

**Spec:** `docs/superpowers/specs/2026-04-15-plugin-system-redesign-design.md`

---

## Phase 0: Worktree Setup

### Task 0.1: Create isolated worktree

**Context:** Main branch has ongoing Phase 1a/1b work. This plan executes in a dedicated worktree to avoid commit contamination.

- [ ] **Step 1: Create worktree**

Run:
```bash
cd /Users/cation98/Project/bedrock-ai-agent
git worktree add ../bedrock-ai-agent-plugins feat/plugin-system-redesign
cd ../bedrock-ai-agent-plugins
```

Expected: new directory `../bedrock-ai-agent-plugins` on branch `feat/plugin-system-redesign`, branching from `main`.

- [ ] **Step 2: Verify branch**

Run: `git branch --show-current`
Expected: `feat/plugin-system-redesign`

- [ ] **Step 3: Verify spec exists**

Run: `ls docs/superpowers/specs/2026-04-15-plugin-system-redesign-design.md`
Expected: file exists

**All subsequent tasks run in `../bedrock-ai-agent-plugins`.**

---

## Phase 1: Gitea Infrastructure

### Task 1.1: Provision RDS PostgreSQL for Gitea

**Files:**
- Create: `infra/terraform/gitea-rds.tf`
- Modify: `infra/terraform/variables.tf` (add gitea_db_password variable)

**Context:** Gitea needs PostgreSQL. Per CLAUDE.md, stateful-set 내장 DB 금지. Use separate RDS instance (not shared with safety-prod).

- [ ] **Step 1: Write Terraform for RDS**

Create `infra/terraform/gitea-rds.tf`:

```hcl
resource "aws_db_instance" "gitea" {
  identifier     = "gitea-postgres"
  engine         = "postgres"
  engine_version = "16.3"
  instance_class = "db.t3.medium"

  allocated_storage     = 100
  max_allocated_storage = 500
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "gitea"
  username = "gitea"
  password = var.gitea_db_password

  vpc_security_group_ids = [aws_security_group.gitea_db.id]
  db_subnet_group_name   = data.aws_db_subnet_group.sko.name

  backup_retention_period = 30
  backup_window           = "18:00-19:00"  # KST 03:00-04:00
  skip_final_snapshot     = false
  final_snapshot_identifier = "gitea-postgres-final"

  tags = {
    Owner   = "N1102359"
    Env     = "prod"
    Service = "gitea"
  }
}

resource "aws_security_group" "gitea_db" {
  name   = "gitea-db-sg"
  vpc_id = data.aws_vpc.sko.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [data.aws_security_group.eks_nodes.id]
  }

  tags = {
    Owner   = "N1102359"
    Env     = "prod"
    Service = "gitea"
  }
}

output "gitea_db_endpoint" {
  value = aws_db_instance.gitea.endpoint
}
```

Add to `variables.tf`:
```hcl
variable "gitea_db_password" {
  description = "Gitea Postgres password (from 1Password)"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 2: Store password in 1Password + AWS Secrets Manager**

Run (manual):
```bash
op item create --category=password --vault="Access Keys" --title="Gitea DB Password" password="$(openssl rand -base64 32)"
# Then retrieve and inject
export TF_VAR_gitea_db_password=$(op item get "Gitea DB Password" --fields credential --reveal)
```

- [ ] **Step 3: Apply Terraform**

Run:
```bash
cd infra/terraform
terraform plan -target=aws_db_instance.gitea -target=aws_security_group.gitea_db
terraform apply -target=aws_db_instance.gitea -target=aws_security_group.gitea_db
```
Expected: RDS instance `gitea-postgres` created, ~10 minutes.

- [ ] **Step 4: Verify connectivity**

Run from EKS worker node (or bastion):
```bash
psql -h <endpoint> -U gitea -d gitea -c "SELECT version();"
```
Expected: PostgreSQL 16.3 version string.

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/gitea-rds.tf infra/terraform/variables.tf
git commit -m "infra(gitea): provision dedicated RDS PostgreSQL for Gitea"
```

### Task 1.2: Create Gitea namespace + NetworkPolicy

**Files:**
- Create: `infra/k8s/gitea/namespace.yaml`
- Create: `infra/k8s/gitea/network-policy.yaml`

- [ ] **Step 1: Write namespace manifest**

Create `infra/k8s/gitea/namespace.yaml`:
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: gitea
  labels:
    name: gitea
    app.kubernetes.io/part-of: bedrock-claude-platform
```

- [ ] **Step 2: Write NetworkPolicy**

Create `infra/k8s/gitea/network-policy.yaml`:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: gitea-egress
  namespace: gitea
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: gitea
  policyTypes: [Egress]
  egress:
  # Gitea mirror fetch: github.com
  - to:
    - ipBlock:
        cidr: 0.0.0.0/0
        except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]
    ports:
    - protocol: TCP
      port: 443
  # RDS PostgreSQL
  - to:
    - ipBlock:
        cidr: 0.0.0.0/0
    ports:
    - protocol: TCP
      port: 5432
  # DNS
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
    ports:
    - protocol: UDP
      port: 53
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: gitea-ingress
  namespace: gitea
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: gitea
  policyTypes: [Ingress]
  ingress:
  # User Pods (neo namespace)
  - from:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: neo
    ports:
    - protocol: TCP
      port: 3000
  # Ingress controller
  - from:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: ingress-nginx
    ports:
    - protocol: TCP
      port: 3000
```

- [ ] **Step 3: Apply**

Run:
```bash
kubectl apply -f infra/k8s/gitea/namespace.yaml
kubectl apply -f infra/k8s/gitea/network-policy.yaml
```
Expected: namespace created, 2 NetworkPolicies created.

- [ ] **Step 4: Commit**

```bash
git add infra/k8s/gitea/namespace.yaml infra/k8s/gitea/network-policy.yaml
git commit -m "infra(gitea): create namespace with egress/ingress NetworkPolicy"
```

### Task 1.3: Deploy Gitea via Helm

**Files:**
- Create: `infra/k8s/gitea/values.yaml`
- Create: `infra/k8s/gitea/install.sh` (helper script)

- [ ] **Step 1: Create Kubernetes Secret for DB password**

Run:
```bash
GITEA_DB_PASSWORD=$(op item get "Gitea DB Password" --fields credential --reveal)
kubectl create secret generic gitea-db-credentials \
  --namespace=gitea \
  --from-literal=password="$GITEA_DB_PASSWORD"
```

- [ ] **Step 2: Write Helm values**

Create `infra/k8s/gitea/values.yaml`:
```yaml
replicaCount: 2

image:
  tag: "1.23"

gitea:
  admin:
    username: gitea_admin
    email: gitea-admin@skons.net
    existingSecret: gitea-admin-credentials
  config:
    server:
      DOMAIN: gitea.internal.skons.net
      ROOT_URL: https://gitea.internal.skons.net
      SSH_DOMAIN: gitea.internal.skons.net
      DISABLE_SSH: true
    service:
      DISABLE_REGISTRATION: true
      REQUIRE_SIGNIN_VIEW: true
      ENABLE_REVERSE_PROXY_AUTHENTICATION: false
    security:
      INSTALL_LOCK: true
    lfs:
      ENABLED: true
    log:
      LEVEL: Info
    mirror:
      ENABLED: true
      DEFAULT_INTERVAL: 6h

postgresql-ha:
  enabled: false
postgresql:
  enabled: false

# Use external RDS
gitea:
  database:
    builtIn:
      postgresql:
        enabled: false

# Override DB connection via env (existing RDS)
# Helm chart reads from GITEA__database__*
extraEnvFrom:
- secretRef:
    name: gitea-db-credentials

# Nodegroup placement (matches CLAUDE.md design constraint)
nodeSelector:
  role: system
tolerations:
- key: dedicated
  operator: Equal
  value: system
  effect: NoSchedule

affinity:
  podAntiAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
    - labelSelector:
        matchLabels:
          app.kubernetes.io/name: gitea
      topologyKey: kubernetes.io/hostname

persistence:
  enabled: true
  size: 500Gi
  storageClass: gp3

service:
  http:
    type: ClusterIP
    port: 3000

ingress:
  enabled: true
  className: nginx
  hosts:
  - host: gitea.internal.skons.net
    paths:
    - path: /
      pathType: Prefix
  tls:
  - secretName: gitea-tls
    hosts: [gitea.internal.skons.net]

resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: 2000m
    memory: 4Gi
```

- [ ] **Step 3: Create admin credentials secret**

Run:
```bash
ADMIN_PASSWORD=$(openssl rand -base64 24)
op item create --category=password --vault="Access Keys" --title="Gitea Admin Password" password="$ADMIN_PASSWORD"
kubectl create secret generic gitea-admin-credentials \
  --namespace=gitea \
  --from-literal=username=gitea_admin \
  --from-literal=password="$ADMIN_PASSWORD"
```

- [ ] **Step 4: Create DB connection secret (references RDS)**

Run:
```bash
RDS_ENDPOINT=$(terraform -chdir=infra/terraform output -raw gitea_db_endpoint)
kubectl create secret generic gitea-db-credentials \
  --namespace=gitea \
  --from-literal=GITEA__database__DB_TYPE=postgres \
  --from-literal=GITEA__database__HOST="$RDS_ENDPOINT" \
  --from-literal=GITEA__database__NAME=gitea \
  --from-literal=GITEA__database__USER=gitea \
  --from-literal=GITEA__database__PASSWD="$GITEA_DB_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -
```

- [ ] **Step 5: Install via Helm**

Run:
```bash
helm repo add gitea-charts https://dl.gitea.com/charts/
helm repo update
helm install gitea gitea-charts/gitea \
  --namespace gitea \
  --values infra/k8s/gitea/values.yaml \
  --version 10.4.x  # pin to latest 1.23-compatible chart
```
Expected: 2 gitea pods Running after ~3 minutes.

- [ ] **Step 6: Verify**

Run:
```bash
kubectl get pods -n gitea
curl -k https://gitea.internal.skons.net/api/healthz
```
Expected: `{"status":"pass"}`

- [ ] **Step 7: Commit**

```bash
git add infra/k8s/gitea/values.yaml
git commit -m "infra(gitea): deploy Gitea HA via Helm with RDS backend"
```

### Task 1.4: Configure SSO OAuth2 provider in Gitea

**Context:** Gitea must authenticate users via sso.skons.net. Use Gitea admin CLI or API.

- [ ] **Step 1: Add OAuth2 provider**

Run:
```bash
kubectl exec -n gitea deploy/gitea -- gitea admin auth add-oauth \
  --name sso-skons \
  --provider openidConnect \
  --key "$SSO_CLIENT_ID" \
  --secret "$SSO_CLIENT_SECRET" \
  --auto-discover-url "https://sso.skons.net/.well-known/openid-configuration"
```

- [ ] **Step 2: Verify login flow**

Manual: open `https://gitea.internal.skons.net/user/login`, click "Sign in with sso-skons", complete SSO, confirm Gitea user created with SSO ID.

- [ ] **Step 3: Commit (if any config files changed)**

No files; skip commit for this task.

---

## Phase 2: Plugin Mirror Setup

### Task 2.1: Create mirror repositories in Gitea

**Files:** none (admin API calls)

- [ ] **Step 1: Get admin token**

Run:
```bash
ADMIN_TOKEN=$(curl -s -u gitea_admin:$ADMIN_PASSWORD \
  -X POST https://gitea.internal.skons.net/api/v1/users/gitea_admin/tokens \
  -H "Content-Type: application/json" \
  -d '{"name":"mirror-setup","scopes":["write:admin","write:repository"]}' \
  | jq -r .sha1)
```

- [ ] **Step 2: Create `mirrors` organization**

Run:
```bash
curl -X POST https://gitea.internal.skons.net/api/v1/orgs \
  -H "Authorization: token $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"mirrors","full_name":"Plugin Marketplace Mirrors"}'
```

- [ ] **Step 3: Create mirror for claude-plugins-official**

Run:
```bash
curl -X POST https://gitea.internal.skons.net/api/v1/repos/migrate \
  -H "Authorization: token $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_name": "claude-plugins-official",
    "repo_owner": "mirrors",
    "clone_addr": "https://github.com/anthropics/claude-plugins-official",
    "mirror": true,
    "mirror_interval": "6h0m0s",
    "description": "Mirror of anthropics/claude-plugins-official"
  }'
```
Expected: HTTP 201, repo visible at `gitea.internal.skons.net/mirrors/claude-plugins-official`.

- [ ] **Step 4: Create mirror for superpowers-marketplace**

Run:
```bash
curl -X POST https://gitea.internal.skons.net/api/v1/repos/migrate \
  -H "Authorization: token $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_name": "superpowers-marketplace",
    "repo_owner": "mirrors",
    "clone_addr": "https://github.com/obra/superpowers-marketplace",
    "mirror": true,
    "mirror_interval": "6h0m0s",
    "description": "Mirror of obra/superpowers-marketplace"
  }'
```

- [ ] **Step 5: Verify first sync**

Run:
```bash
curl -H "Authorization: token $ADMIN_TOKEN" \
  https://gitea.internal.skons.net/api/v1/repos/mirrors/claude-plugins-official | jq .updated_at
```
Expected: timestamp within last 5 minutes.

### Task 2.2: Mirror sync monitoring CronJob

**Files:**
- Create: `infra/k8s/gitea/mirror-monitor-cronjob.yaml`
- Create: `infra/k8s/gitea/mirror-monitor.sh`

- [ ] **Step 1: Write monitor script**

Create `infra/k8s/gitea/mirror-monitor.sh`:
```bash
#!/bin/bash
set -euo pipefail

GITEA_URL="${GITEA_URL:-https://gitea.internal.skons.net}"
TOKEN="$GITEA_ADMIN_TOKEN"
SLACK_WEBHOOK="$SLACK_WEBHOOK_URL"
REPOS=("mirrors/claude-plugins-official" "mirrors/superpowers-marketplace")

now=$(date +%s)
threshold=$((now - 86400))  # 24h

for repo in "${REPOS[@]}"; do
  updated=$(curl -sf -H "Authorization: token $TOKEN" \
    "$GITEA_URL/api/v1/repos/$repo" | jq -r .updated_at)
  updated_ts=$(date -d "$updated" +%s 2>/dev/null || date -jf "%Y-%m-%dT%H:%M:%SZ" "$updated" +%s)

  if [[ $updated_ts -lt $threshold ]]; then
    curl -X POST "$SLACK_WEBHOOK" \
      -H "Content-Type: application/json" \
      -d "{\"text\":\":warning: Gitea mirror $repo has not synced in 24h (last: $updated)\"}"
  fi
done
```

- [ ] **Step 2: Create ConfigMap + CronJob**

Create `infra/k8s/gitea/mirror-monitor-cronjob.yaml`:
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: mirror-monitor-script
  namespace: gitea
data:
  monitor.sh: |
    # (content of mirror-monitor.sh here, or mount from configmap)
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: mirror-monitor
  namespace: gitea
spec:
  schedule: "0 */6 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
          - name: monitor
            image: curlimages/curl:8.6.0
            command: ["/bin/sh", "/scripts/monitor.sh"]
            env:
            - name: GITEA_URL
              value: "https://gitea.internal.skons.net"
            - name: GITEA_ADMIN_TOKEN
              valueFrom:
                secretKeyRef:
                  name: gitea-admin-token
                  key: token
            - name: SLACK_WEBHOOK_URL
              valueFrom:
                secretKeyRef:
                  name: slack-webhook
                  key: url
            volumeMounts:
            - name: scripts
              mountPath: /scripts
          volumes:
          - name: scripts
            configMap:
              name: mirror-monitor-script
              defaultMode: 0755
```

- [ ] **Step 3: Create required secrets**

Run:
```bash
kubectl create secret generic gitea-admin-token \
  --namespace=gitea \
  --from-literal=token="$ADMIN_TOKEN"

kubectl create secret generic slack-webhook \
  --namespace=gitea \
  --from-literal=url="$SLACK_WEBHOOK_URL"
```

- [ ] **Step 4: Apply**

Run: `kubectl apply -f infra/k8s/gitea/mirror-monitor-cronjob.yaml`

- [ ] **Step 5: Test manual trigger**

Run:
```bash
kubectl create job --from=cronjob/mirror-monitor mirror-monitor-test -n gitea
kubectl logs -n gitea job/mirror-monitor-test
```
Expected: exits 0, no Slack alert (mirrors just synced).

- [ ] **Step 6: Commit**

```bash
git add infra/k8s/gitea/mirror-monitor-cronjob.yaml infra/k8s/gitea/mirror-monitor.sh
git commit -m "infra(gitea): add 6h mirror sync monitoring CronJob with Slack alerts"
```

---

## Phase 3: User Pod Plugin Fix

### Task 3.1: Pin plugin versions

**Files:**
- Modify: `container-image/Dockerfile`

**Context:** Current Dockerfile hardcodes `superpowers/4.0.3`. Host has 5.0.7. Other plugins (frontend-design, feature-dev) use hash-based versions in host cache. We need to identify canonical versions from marketplace manifest.

- [ ] **Step 1: Query Gitea mirror for plugin manifest**

Run:
```bash
curl -s https://gitea.internal.skons.net/mirrors/claude-plugins-official/raw/branch/main/.claude-plugin/marketplace.json | jq
```
Expected: JSON with plugins array. Record the `version` field for superpowers, frontend-design, feature-dev.

- [ ] **Step 2: Add ARG declarations to Dockerfile**

In `container-image/Dockerfile`, add after existing ARGs (or at top):
```dockerfile
ARG SUPERPOWERS_VERSION=5.0.7
ARG FRONTEND_DESIGN_VERSION=<version-from-step-1>
ARG FEATURE_DEV_VERSION=<version-from-step-1>
```

- [ ] **Step 3: Commit**

```bash
git add container-image/Dockerfile
git commit -m "container: pin plugin versions via Dockerfile ARGs"
```

### Task 3.2: Fix `installed_plugins.json`

**Files:**
- Modify: `container-image/config/installed_plugins.json`

- [ ] **Step 1: Write correct content**

Replace file contents:
```json
{
  "version": "1.0",
  "plugins": {
    "superpowers@claude-plugins-official": [
      {
        "scope": "user",
        "installPath": "/home/node/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.7",
        "version": "5.0.7",
        "installedAt": "2026-04-15T00:00:00.000Z",
        "lastUpdated": "2026-04-15T00:00:00.000Z"
      }
    ],
    "frontend-design@claude-plugins-official": [
      {
        "scope": "user",
        "installPath": "/home/node/.claude/plugins/cache/claude-plugins-official/frontend-design/<VERSION>",
        "version": "<VERSION>",
        "installedAt": "2026-04-15T00:00:00.000Z",
        "lastUpdated": "2026-04-15T00:00:00.000Z"
      }
    ],
    "feature-dev@claude-plugins-official": [
      {
        "scope": "user",
        "installPath": "/home/node/.claude/plugins/cache/claude-plugins-official/feature-dev/<VERSION>",
        "version": "<VERSION>",
        "installedAt": "2026-04-15T00:00:00.000Z",
        "lastUpdated": "2026-04-15T00:00:00.000Z"
      }
    ]
  }
}
```

Replace `<VERSION>` with actual values recorded in Task 3.1 Step 1.

- [ ] **Step 2: Validate JSON**

Run: `jq . container-image/config/installed_plugins.json`
Expected: parsed output, no errors.

- [ ] **Step 3: Commit**

```bash
git add container-image/config/installed_plugins.json
git commit -m "container: register 3 plugins in installed_plugins.json (remove serena)"
```

### Task 3.3: Fix `plugins-config.json` (enable flags)

**Files:**
- Modify: `container-image/config/plugins-config.json`

**Context:** This is the root cause of plugins not loading. Must have `enabled: true` per plugin.

- [ ] **Step 1: Write correct content**

Replace file contents:
```json
{
  "repositories": {
    "claude-plugins-official": {
      "enabled": true,
      "plugins": {
        "superpowers": { "enabled": true },
        "frontend-design": { "enabled": true },
        "feature-dev": { "enabled": true }
      }
    }
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add container-image/config/plugins-config.json
git commit -m "container(fix): enable 3 plugins in plugins-config.json — root cause of plugins not loading"
```

### Task 3.4: Fix `plugins-known-marketplaces.json` (Gitea-only)

**Files:**
- Modify: `container-image/config/plugins-known-marketplaces.json`

- [ ] **Step 1: Write correct content**

Replace file contents:
```json
{
  "claude-plugins-official": {
    "source": {
      "source": "git",
      "url": "https://gitea.internal.skons.net/mirrors/claude-plugins-official.git"
    },
    "installLocation": "/home/node/.claude/plugins/marketplaces/claude-plugins-official",
    "lastUpdated": "2026-04-15T00:00:00.000Z"
  },
  "superpowers-marketplace": {
    "source": {
      "source": "git",
      "url": "https://gitea.internal.skons.net/mirrors/superpowers-marketplace.git"
    },
    "installLocation": "/home/node/.claude/plugins/marketplaces/superpowers-marketplace",
    "lastUpdated": "2026-04-15T00:00:00.000Z"
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add container-image/config/plugins-known-marketplaces.json
git commit -m "container: replace GitHub marketplace sources with Gitea mirror URLs"
```

### Task 3.5: Clear blocklist test data

**Files:**
- Modify: `container-image/config/plugins-blocklist.json`

- [ ] **Step 1: Reset to empty**

Replace file contents:
```json
{
  "fetchedAt": "2026-04-15T00:00:00.000Z",
  "plugins": []
}
```

- [ ] **Step 2: Commit**

```bash
git add container-image/config/plugins-blocklist.json
git commit -m "container: clear test data from plugins-blocklist.json"
```

### Task 3.6: Remove bundled marketplace + serena

**Files:**
- Delete: `container-image/config/plugins-marketplaces/`
- Delete: `container-image/config/plugins/serena/` (if exists)

- [ ] **Step 1: Delete directories**

Run:
```bash
git rm -rf container-image/config/plugins-marketplaces/
git rm -rf container-image/config/plugins/serena/ 2>/dev/null || true
```

- [ ] **Step 2: Commit**

```bash
git commit -m "container: remove bundled marketplace repo and serena (Gitea handles mirror; serena Phase 2)"
```

### Task 3.7: Update Dockerfile plugin COPY directives

**Files:**
- Modify: `container-image/Dockerfile:136-143`

- [ ] **Step 1: Update plugin COPY block**

Replace lines 136-143 (approx) in `Dockerfile`:
```dockerfile
# Before (current, broken):
# COPY --chown=node:node config/plugins/superpowers/ /home/node/.claude/plugins/cache/superpowers-marketplace/superpowers/4.0.3/
# COPY --chown=node:node config/plugins/serena/ /home/node/.claude/plugins/cache/claude-plugins-official/serena/unknown/
# COPY --chown=node:node config/plugins-marketplaces/ /home/node/.claude/plugins/marketplaces/

# After:
COPY --chown=node:node config/plugins/superpowers/     /home/node/.claude/plugins/cache/claude-plugins-official/superpowers/${SUPERPOWERS_VERSION}/
COPY --chown=node:node config/plugins/frontend-design/ /home/node/.claude/plugins/cache/claude-plugins-official/frontend-design/${FRONTEND_DESIGN_VERSION}/
COPY --chown=node:node config/plugins/feature-dev/     /home/node/.claude/plugins/cache/claude-plugins-official/feature-dev/${FEATURE_DEV_VERSION}/
COPY --chown=node:node config/installed_plugins.json   /home/node/.claude/plugins/installed_plugins.json
COPY --chown=node:node config/plugins-config.json      /home/node/.claude/plugins/config.json
COPY --chown=node:node config/plugins-blocklist.json   /home/node/.claude/plugins/blocklist.json
COPY --chown=node:node config/plugins-known-marketplaces.json /home/node/.claude/plugins/known_marketplaces.json
COPY --chown=node:node config/plugins-data/            /home/node/.claude/plugins/data/
# Note: plugins-marketplaces/ removed — Gitea serves as source
```

- [ ] **Step 2: Sync plugin files from host cache**

Run:
```bash
# Remove old superpowers
rm -rf container-image/config/plugins/superpowers
# Copy current versions
cp -r ~/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.7 container-image/config/plugins/superpowers
cp -r ~/.claude/plugins/cache/claude-plugins-official/frontend-design/<VERSION> container-image/config/plugins/frontend-design
cp -r ~/.claude/plugins/cache/claude-plugins-official/feature-dev/<VERSION> container-image/config/plugins/feature-dev
# Verify plugin manifests exist
ls container-image/config/plugins/*/\.claude-plugin/plugin.json 2>/dev/null || ls container-image/config/plugins/*/plugin.json
```

- [ ] **Step 3: Build image locally**

Run:
```bash
cd container-image
docker build --platform linux/amd64 \
  --build-arg SUPERPOWERS_VERSION=5.0.7 \
  --build-arg FRONTEND_DESIGN_VERSION=<VERSION> \
  --build-arg FEATURE_DEV_VERSION=<VERSION> \
  -t claude-code-terminal:test .
```
Expected: successful build.

- [ ] **Step 4: Run container and verify /plugin UI**

Run:
```bash
docker run --rm -p 7681:7681 claude-code-terminal:test
# Open http://localhost:7681, run Claude Code, type /plugin
```
Expected: Installed tab shows 3 plugins (superpowers, frontend-design, feature-dev) all with "enabled" status. Errors tab clean.

- [ ] **Step 5: Commit**

```bash
git add container-image/Dockerfile container-image/config/plugins/
git commit -m "container: bundle 3 plugins at correct paths with version ARGs"
```

---

## Phase 4: User Pod git Integration

### Task 4.1: pre-push hook

**Files:**
- Create: `container-image/config/git-hooks/pre-push`

- [ ] **Step 1: Write hook**

Create `container-image/config/git-hooks/pre-push`:
```bash
#!/bin/bash
# Block push to anywhere except Gitea internal server
set -euo pipefail

remote_name="$1"
remote_url="$2"

ALLOWED_PREFIX="https://gitea.internal.skons.net/"

if [[ "$remote_url" != "$ALLOWED_PREFIX"* ]]; then
  echo "❌ Push blocked: remote URL ($remote_url) is not Gitea (gitea.internal.skons.net)."
  echo "   External git push is prohibited by platform policy."
  echo "   Configure remote to use Gitea: git remote set-url origin https://gitea.internal.skons.net/users/<your-id>/<repo>.git"
  exit 1
fi

exit 0
```

- [ ] **Step 2: Make executable and commit**

Run:
```bash
chmod +x container-image/config/git-hooks/pre-push
git add container-image/config/git-hooks/pre-push
git commit -m "container: pre-push hook blocking non-Gitea remotes"
```

### Task 4.2: gitconfig template

**Files:**
- Create: `container-image/config/gitconfig.template`

- [ ] **Step 1: Write template**

Create `container-image/config/gitconfig.template`:
```ini
[user]
    name = ${GITEA_USER}
    email = ${GITEA_USER}@skons.net

[url "https://gitea.internal.skons.net/mirrors/"]
    insteadOf = https://github.com/

[credential "https://gitea.internal.skons.net"]
    helper = store

[core]
    hooksPath = /usr/local/share/git-hooks

[init]
    defaultBranch = main
```

- [ ] **Step 2: Update Dockerfile to install hooks globally**

Add to `container-image/Dockerfile` (before entrypoint line):
```dockerfile
COPY --chown=root:root config/git-hooks/ /usr/local/share/git-hooks/
RUN chmod +x /usr/local/share/git-hooks/* && chmod 755 /usr/local/share/git-hooks

COPY --chown=node:node config/gitconfig.template /home/node/.gitconfig.template
```

- [ ] **Step 3: Commit**

```bash
git add container-image/config/gitconfig.template container-image/Dockerfile
git commit -m "container: gitconfig template and global git hooks installation"
```

### Task 4.3: entrypoint.sh git config setup

**Files:**
- Modify: `container-image/entrypoint.sh`

- [ ] **Step 1: Read current entrypoint**

Run: `cat container-image/entrypoint.sh`

- [ ] **Step 2: Add git setup block before ttyd start**

Insert (at appropriate location per existing structure):
```bash
# --- Gitea git setup ---
if [[ -n "${GITEA_USER:-}" && -n "${GITEA_TOKEN:-}" ]]; then
  # Generate .gitconfig from template
  envsubst < /home/node/.gitconfig.template > /home/node/.gitconfig
  chmod 600 /home/node/.gitconfig

  # Store credentials
  echo "https://${GITEA_USER}:${GITEA_TOKEN}@gitea.internal.skons.net" > /home/node/.git-credentials
  chmod 600 /home/node/.git-credentials

  echo "[entrypoint] Gitea git configured for user: $GITEA_USER"
else
  echo "[entrypoint] WARNING: GITEA_USER/GITEA_TOKEN not set — git operations will fail"
fi
# --- end ---
```

- [ ] **Step 3: Verify envsubst available**

Ensure `Dockerfile` installs `gettext-base` (provides envsubst):
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends gettext-base && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 4: Local test**

Run:
```bash
docker run --rm \
  -e GITEA_USER=testuser \
  -e GITEA_TOKEN=testtoken \
  -p 7681:7681 \
  claude-code-terminal:test \
  bash -c "cat ~/.gitconfig && cat ~/.git-credentials"
```
Expected: gitconfig with testuser, credentials file populated.

- [ ] **Step 5: Commit**

```bash
git add container-image/entrypoint.sh container-image/Dockerfile
git commit -m "container: entrypoint generates .gitconfig from GITEA_* env"
```

---

## Phase 5: Auth Gateway — Gitea Provisioning

### Task 5.1: Gitea client service (TDD)

**Files:**
- Create: `auth-gateway/app/services/gitea_client.py`
- Create: `auth-gateway/tests/services/test_gitea_client.py`

- [ ] **Step 1: Write failing test**

Create `auth-gateway/tests/services/test_gitea_client.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from app.services.gitea_client import GiteaClient, GiteaUserInfo


@pytest.fixture
def client():
    return GiteaClient(
        base_url="https://gitea.test",
        admin_token="admin-token-xyz",
    )


def test_ensure_user_creates_when_missing(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.get.return_value = MagicMock(status_code=404)
        instance.post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"id": 42, "login": "N1102359"},
        )

        info = client.ensure_user(sso_id="N1102359", email="user@skons.net")

    assert info.login == "N1102359"
    assert info.id == 42
    assert instance.post.called


def test_ensure_user_returns_existing(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": 42, "login": "N1102359"},
        )

        info = client.ensure_user(sso_id="N1102359", email="user@skons.net")

    assert info.login == "N1102359"
    assert not instance.post.called


def test_issue_token_returns_sha1(client):
    with patch("app.services.gitea_client.httpx.Client") as mock_httpx:
        instance = mock_httpx.return_value.__enter__.return_value
        instance.post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"sha1": "abc123", "name": "session-1"},
        )

        token = client.issue_user_token(user_login="N1102359", token_name="session-1")

    assert token == "abc123"
```

- [ ] **Step 2: Run test — expect failure**

Run: `cd auth-gateway && pytest tests/services/test_gitea_client.py -v`
Expected: ImportError (module not yet created).

- [ ] **Step 3: Implement GiteaClient**

Create `auth-gateway/app/services/gitea_client.py`:
```python
"""Gitea admin client for user provisioning and token management."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import httpx


@dataclass
class GiteaUserInfo:
    id: int
    login: str


class GiteaProvisioningError(Exception):
    pass


class GiteaClient:
    def __init__(self, base_url: str, admin_token: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"token {admin_token}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    def ensure_user(self, sso_id: str, email: str) -> GiteaUserInfo:
        """Return existing user or create new. Idempotent."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                f"{self._base_url}/api/v1/users/{sso_id}",
                headers=self._headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                return GiteaUserInfo(id=data["id"], login=data["login"])

            if resp.status_code != 404:
                raise GiteaProvisioningError(
                    f"Unexpected response from Gitea: {resp.status_code}"
                )

            # Create user
            create_resp = client.post(
                f"{self._base_url}/api/v1/admin/users",
                headers=self._headers,
                json={
                    "username": sso_id,
                    "email": email,
                    "password": self._random_password(),
                    "must_change_password": False,
                    "send_notify": False,
                    "source_id": 0,
                },
            )
            if create_resp.status_code != 201:
                raise GiteaProvisioningError(
                    f"Failed to create user {sso_id}: {create_resp.status_code} {create_resp.text}"
                )
            data = create_resp.json()
            return GiteaUserInfo(id=data["id"], login=data["login"])

    def issue_user_token(self, user_login: str, token_name: str) -> str:
        """Issue a new access token for a user. Returns sha1."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self._base_url}/api/v1/admin/users/{user_login}/tokens",
                headers=self._headers,
                json={
                    "name": token_name,
                    "scopes": ["write:repository", "read:user"],
                },
            )
            if resp.status_code != 201:
                raise GiteaProvisioningError(
                    f"Failed to issue token for {user_login}: {resp.status_code} {resp.text}"
                )
            return resp.json()["sha1"]

    @staticmethod
    def _random_password() -> str:
        import secrets
        return secrets.token_urlsafe(24)
```

- [ ] **Step 4: Run test — expect pass**

Run: `cd auth-gateway && pytest tests/services/test_gitea_client.py -v`
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add auth-gateway/app/services/gitea_client.py auth-gateway/tests/services/test_gitea_client.py
git commit -m "auth-gateway(feat): GiteaClient for user provisioning + token issuance"
```

### Task 5.2: Integrate Gitea provisioning into Pod spawn

**Files:**
- Modify: `auth-gateway/app/services/k8s_service.py` (env injection)
- Modify: `auth-gateway/app/routers/sessions.py` or wherever Pod spawn is triggered

- [ ] **Step 1: Locate Pod spawn entrypoint**

Run: `grep -rn "create_pod\|create_namespaced_pod\|spawn" auth-gateway/app/services/k8s_service.py | head -20`
Record: the function that spawns User Pods and accepts env vars.

- [ ] **Step 2: Write failing integration test**

Add to `auth-gateway/tests/services/test_k8s_service.py` (or create):
```python
def test_spawn_pod_injects_gitea_env(monkeypatch, k8s_service):
    gitea_client = MagicMock()
    gitea_client.ensure_user.return_value = GiteaUserInfo(id=1, login="N1102359")
    gitea_client.issue_user_token.return_value = "session-token-abc"

    monkeypatch.setattr("app.services.k8s_service.get_gitea_client", lambda: gitea_client)

    pod = k8s_service.spawn_user_pod(sso_id="N1102359", email="user@skons.net")
    env_map = {e.name: e.value for e in pod.spec.containers[0].env}

    assert env_map["GITEA_URL"] == "https://gitea.internal.skons.net"
    assert env_map["GITEA_USER"] == "N1102359"
    assert env_map["GITEA_TOKEN"] == "session-token-abc"
```

- [ ] **Step 3: Run test — expect fail**

Run: `pytest auth-gateway/tests/services/test_k8s_service.py -v`

- [ ] **Step 4: Implement injection**

In `auth-gateway/app/services/k8s_service.py` (or equivalent):
```python
from app.services.gitea_client import GiteaClient, GiteaProvisioningError

def get_gitea_client() -> GiteaClient:
    from app.core.config import settings
    return GiteaClient(
        base_url=settings.GITEA_URL,
        admin_token=settings.GITEA_ADMIN_TOKEN,
    )

def _gitea_env_for(sso_id: str, email: str) -> list[dict]:
    """Provision Gitea user and return env vars for Pod."""
    client = get_gitea_client()
    try:
        user = client.ensure_user(sso_id=sso_id, email=email)
        token = client.issue_user_token(
            user_login=user.login,
            token_name=f"session-{uuid.uuid4().hex[:8]}",
        )
    except GiteaProvisioningError as e:
        logger.error(f"Gitea provisioning failed for {sso_id}: {e}")
        raise

    return [
        {"name": "GITEA_URL", "value": settings.GITEA_URL},
        {"name": "GITEA_USER", "value": user.login},
        {"name": "GITEA_TOKEN", "value": token},
    ]
```

Modify `spawn_user_pod` to call `_gitea_env_for()` and append result to container env list.

- [ ] **Step 5: Add settings**

In `auth-gateway/app/core/config.py`:
```python
GITEA_URL: str = "https://gitea.internal.skons.net"
GITEA_ADMIN_TOKEN: str  # from secret
```

- [ ] **Step 6: Create K8s Secret for admin token**

Run:
```bash
kubectl create secret generic gitea-admin-api-token \
  --namespace=neo \
  --from-literal=token="$ADMIN_TOKEN"
```

Add to auth-gateway Deployment:
```yaml
env:
- name: GITEA_ADMIN_TOKEN
  valueFrom:
    secretKeyRef:
      name: gitea-admin-api-token
      key: token
```

- [ ] **Step 7: Run tests — expect pass**

Run: `pytest auth-gateway/tests/ -v`

- [ ] **Step 8: Commit**

```bash
git add auth-gateway/
git commit -m "auth-gateway: provision Gitea user and inject GITEA_* env on Pod spawn"
```

### Task 5.3: Deploy updated auth-gateway

- [ ] **Step 1: Build and push auth-gateway image**

Run:
```bash
cd auth-gateway
docker build --platform linux/amd64 -t <REGISTRY>/auth-gateway:$(git rev-parse --short HEAD) .
docker push <REGISTRY>/auth-gateway:$(git rev-parse --short HEAD)
```

- [ ] **Step 2: Update auth-gateway Deployment**

Run:
```bash
kubectl set image -n neo deployment/auth-gateway auth-gateway=<REGISTRY>/auth-gateway:<tag>
kubectl rollout status -n neo deployment/auth-gateway
```
Expected: rollout successful, 2 replicas Running.

- [ ] **Step 3: Manual end-to-end check**

Steps: SSO login → check Gitea user exists → spawn Pod → `kubectl exec` into Pod → verify `env | grep GITEA`.

---

## Phase 6: NetworkPolicy for User Pods

### Task 6.1: User Pod egress restriction

**Files:**
- Create: `infra/k8s/user-pods/network-policy.yaml`

- [ ] **Step 1: Identify User Pod namespace + label**

Run: `kubectl get pods -n neo --show-labels | head`
Record: namespace = `neo`, Pod label selector (e.g., `app=user-terminal`).

- [ ] **Step 2: Write NetworkPolicy**

Create `infra/k8s/user-pods/network-policy.yaml`:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: user-pod-egress
  namespace: neo
spec:
  podSelector:
    matchLabels:
      app: user-terminal  # adjust to actual label
  policyTypes: [Egress]
  egress:
  # Gitea
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: gitea
    ports:
    - protocol: TCP
      port: 3000
  # Auth Gateway (same namespace)
  - to:
    - podSelector:
        matchLabels:
          app: auth-gateway
    ports:
    - protocol: TCP
      port: 8000
  # DNS
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
    ports:
    - protocol: UDP
      port: 53
  # AWS Bedrock / RDS / SSO — require explicit IP or endpoint
  # Bedrock: goes through VPC endpoint (internal IP range)
  - to:
    - ipBlock:
        cidr: 10.0.0.0/16  # adjust to actual VPC CIDR for VPC endpoints
    ports:
    - protocol: TCP
      port: 443
  # SSO: external but required — add sso.skons.net IP or allow via egress gateway
  # (placeholder; requires DNS-based policy or egress proxy — see follow-up)
```

- [ ] **Step 3: Apply in dry-run**

Run:
```bash
kubectl apply -f infra/k8s/user-pods/network-policy.yaml --dry-run=server
```

- [ ] **Step 4: Apply**

Run: `kubectl apply -f infra/k8s/user-pods/network-policy.yaml`

- [ ] **Step 5: Verify egress from test Pod**

Run:
```bash
POD=$(kubectl get pod -n neo -l app=user-terminal -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n neo $POD -- curl -sf -m 5 https://github.com || echo "BLOCKED (expected)"
kubectl exec -n neo $POD -- curl -sf -m 5 https://gitea.internal.skons.net/api/healthz
```
Expected: github.com BLOCKED, Gitea responds.

- [ ] **Step 6: Commit**

```bash
git add infra/k8s/user-pods/network-policy.yaml
git commit -m "infra(neo): User Pod egress restricted to Gitea + internal services"
```

---

## Phase 7: End-to-End Integration Test

### Task 7.1: E2E test script

**Files:**
- Create: `scripts/test-plugin-system-e2e.sh`

- [ ] **Step 1: Write E2E test**

Create `scripts/test-plugin-system-e2e.sh`:
```bash
#!/bin/bash
# End-to-end test for plugin system redesign.
# Run against dev environment.
set -euo pipefail

echo "=== E2E Test: Plugin System ==="

# Acquire a test user session
SSO_TEST_USER="${SSO_TEST_USER:-test-user-001}"
SESSION_TOKEN=$(curl -sf -X POST "$AUTH_GATEWAY_URL/auth/test-login" \
  -d "username=$SSO_TEST_USER" | jq -r .token)
echo "[1] SSO login OK"

# Spawn Pod
POD_NAME=$(curl -sf -X POST "$AUTH_GATEWAY_URL/sessions/spawn" \
  -H "Authorization: Bearer $SESSION_TOKEN" | jq -r .pod_name)
echo "[2] Pod spawned: $POD_NAME"

# Wait ready
kubectl wait -n neo --for=condition=Ready pod/$POD_NAME --timeout=60s
echo "[3] Pod Ready"

# Verify GITEA env
GITEA_TOKEN=$(kubectl exec -n neo $POD_NAME -- sh -c 'echo $GITEA_TOKEN')
[[ -n "$GITEA_TOKEN" ]] || { echo "FAIL: GITEA_TOKEN not set"; exit 1; }
echo "[4] GITEA_TOKEN injected"

# Verify gitconfig
kubectl exec -n neo $POD_NAME -- cat /home/node/.gitconfig | grep -q "gitea.internal.skons.net" || \
  { echo "FAIL: gitconfig missing Gitea URL"; exit 1; }
echo "[5] gitconfig contains Gitea URL"

# Verify 3 plugins installed
PLUGIN_COUNT=$(kubectl exec -n neo $POD_NAME -- \
  jq '.plugins | length' /home/node/.claude/plugins/installed_plugins.json)
[[ "$PLUGIN_COUNT" == "3" ]] || { echo "FAIL: expected 3 plugins, got $PLUGIN_COUNT"; exit 1; }
echo "[6] 3 plugins registered"

# Verify plugins enabled
ENABLED=$(kubectl exec -n neo $POD_NAME -- \
  jq -r '.repositories."claude-plugins-official".enabled' /home/node/.claude/plugins/config.json)
[[ "$ENABLED" == "true" ]] || { echo "FAIL: plugins not enabled"; exit 1; }
echo "[7] Plugins enabled flag set"

# Verify github.com blocked
if kubectl exec -n neo $POD_NAME -- curl -sf -m 5 https://github.com > /dev/null 2>&1; then
  echo "FAIL: github.com reachable (should be blocked)"
  exit 1
fi
echo "[8] github.com correctly blocked"

# Verify Gitea reachable
kubectl exec -n neo $POD_NAME -- curl -sf -m 5 https://gitea.internal.skons.net/api/healthz || \
  { echo "FAIL: Gitea unreachable"; exit 1; }
echo "[9] Gitea reachable"

# Verify push to Gitea works
kubectl exec -n neo $POD_NAME -- bash -c '
  mkdir -p /tmp/testrepo && cd /tmp/testrepo
  git init && echo test > README.md && git add . && git commit -m test
  git remote add origin "https://gitea.internal.skons.net/users/'"$SSO_TEST_USER"'/test-e2e.git"
  curl -sf -X POST -H "Authorization: token $GITEA_TOKEN" \
    "$GITEA_URL/api/v1/user/repos" \
    -d "{\"name\":\"test-e2e\",\"private\":true}"
  git push -u origin main
' || { echo "FAIL: Gitea push failed"; exit 1; }
echo "[10] Push to Gitea succeeded"

# Verify push to github.com fails
if kubectl exec -n neo $POD_NAME -- bash -c '
  cd /tmp/testrepo
  git remote set-url origin https://github.com/test/test.git
  git push 2>&1
' | grep -q "Push blocked\|Could not resolve\|Connection refused\|Connection timed out"; then
  echo "[11] Push to github.com correctly blocked"
else
  echo "FAIL: Push to github.com was not blocked"
  exit 1
fi

echo ""
echo "=== All E2E checks passed ==="
```

- [ ] **Step 2: Make executable and commit**

Run:
```bash
chmod +x scripts/test-plugin-system-e2e.sh
git add scripts/test-plugin-system-e2e.sh
git commit -m "test(plugin): end-to-end verification script"
```

### Task 7.2: Run E2E in dev

- [ ] **Step 1: Execute**

Run: `./scripts/test-plugin-system-e2e.sh`
Expected: all 11 checks pass.

- [ ] **Step 2: If any fails**

Go back to the relevant Phase/Task and fix. Re-run until green.

---

## Phase 8: Rollout

### Task 8.1: Canary

- [ ] **Step 1: Select 1-2 実務者 users**

Manual: coordinate with identified canary users.

- [ ] **Step 2: Issue canary image**

Manual: Point those users to ingress serving canary image (feature flag or separate service).

- [ ] **Step 3: Monitor 1 week**

Check:
- Gitea sync success rate
- Pod plugin load failure rate
- NetworkPolicy violation count
- User-reported issues

### Task 8.2: Full rollout

- [ ] **Step 1: Tag release**

Run: `git tag plugin-system-v1.0 && git push origin plugin-system-v1.0`

- [ ] **Step 2: Rolling restart**

Run: `kubectl rollout restart deployment/auth-gateway -n neo`
Expected: existing User Pods keep running (per CLAUDE.md "no Pod delete on deploy"), new images apply on next re-login.

- [ ] **Step 3: Merge to main**

Run:
```bash
cd ../bedrock-ai-agent-plugins
git push origin feat/plugin-system-redesign
# open PR, merge
```

- [ ] **Step 4: Cleanup worktree**

Run:
```bash
cd /Users/cation98/Project/bedrock-ai-agent
git worktree remove ../bedrock-ai-agent-plugins
```

---

## Post-Implementation

- [ ] Update `mindbase` with lessons learned (category=pattern, name=plugin-system-redesign)
- [ ] Update `MEMORY.md` with pointer
- [ ] Capture architecture changes in Graphiti (add_memory, type=architecture)
- [ ] Update project docs: `container-image/README.md`, `infra/k8s/README.md` (if exists)
