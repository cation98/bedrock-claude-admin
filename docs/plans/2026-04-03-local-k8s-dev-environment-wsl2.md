# Local Kubernetes Development Environment (Windows 11 WSL2)

**Date**: 2026-04-03
**Status**: Draft
**Author**: System Architect
**Constraint**: No Docker Desktop (enterprise license restriction)

---

## 1. Architecture Overview

```
Windows 11 Host (Browser)
  |
  |  http://claude.local:8000   (auth-gateway)
  |  http://claude.local:3000   (admin-dashboard dev server)
  |  http://claude.local:7681   (ttyd terminal — via ingress)
  |
  +-- WSL2 (Ubuntu 22.04+) ─────────────────────────────────────
  |     |
  |     +-- k3s (lightweight K8s) ─── single-node cluster
  |     |     |
  |     |     +-- Namespace: platform
  |     |     |     +-- auth-gateway (Deployment, 1 replica)
  |     |     |     +-- auth-gateway Service (ClusterIP :80 -> :8000)
  |     |     |
  |     |     +-- Namespace: claude-sessions
  |     |     |     +-- claude-terminal-{user} Pods (on-demand)
  |     |     |     +-- Per-pod Services (ttyd:7681, files:8080, webapp:3000)
  |     |     |     +-- efs-shared-pvc (hostPath volume)
  |     |     |
  |     |     +-- Namespace: ingress-nginx
  |     |     |     +-- ingress-nginx-controller (NodePort)
  |     |     |
  |     |     +-- Namespace: database
  |     |           +-- PostgreSQL 15 (StatefulSet + PVC)
  |     |
  |     +-- nerdctl / containerd ─── container build & runtime
  |     |
  |     +-- Auth Gateway source ─── bind-mounted for hot-reload
  |     +-- AWS credentials ─── ~/.aws/credentials (SSO or IAM)
  |
  +-- Admin Dashboard ─── runs natively (npm run dev, port 3000)
       (or inside WSL2 — developer choice)
```

### Key Design Decisions

1. **k3s over minikube/kind**: k3s runs natively in WSL2 without nested virtualization, ships with containerd (no Docker needed), uses ~512MB RAM baseline, and includes a built-in local storage provisioner. minikube requires Docker driver or extra VM layers on WSL2. kind requires Docker. k3s is the only option that satisfies all constraints cleanly.

2. **nerdctl over docker CLI**: nerdctl is a Docker-compatible CLI for containerd. Since k3s ships with containerd, nerdctl provides `docker build`-equivalent commands (`nerdctl build`) without Docker Desktop. Images built with nerdctl are immediately available to k3s pods through the shared containerd socket.

3. **Admin Dashboard runs outside the cluster**: The admin dashboard is a Next.js dev server. Running it natively (or in WSL2 directly) gives instant hot-reload without container rebuild overhead. It connects to auth-gateway via localhost port-forward.

4. **PostgreSQL inside the cluster**: Running PostgreSQL as a StatefulSet keeps the setup self-contained. A single `k3s-start.sh` brings everything up including the database.

---

## 2. Component List

### Container Runtime & Orchestration

| Component | Version | License | Purpose | Install Method |
|-----------|---------|---------|---------|----------------|
| k3s | v1.31.x | Apache-2.0 | Lightweight K8s distribution | `curl -sfL https://get.k3s.io \| sh -` |
| nerdctl | v2.0.x | Apache-2.0 | Docker-compatible CLI for containerd | `wget` from GitHub releases |
| buildkit | v0.18.x | Apache-2.0 | Container image builder (nerdctl dependency) | Bundled with nerdctl-full |

### Cluster Components (Deployed via kubectl/Helm)

| Component | Version | Purpose | Production Equivalent |
|-----------|---------|---------|----------------------|
| ingress-nginx | v1.12.x | HTTP routing + WebSocket proxy | ingress-nginx on EKS |
| PostgreSQL 15 | 15-alpine | Platform database | RDS PostgreSQL |
| Local Path Provisioner | built-in (k3s) | PersistentVolume storage | EFS CSI Driver |

### Host-Level Tools

| Component | Version | Purpose |
|-----------|---------|---------|
| kubectl | v1.31.x | K8s CLI (bundled with k3s) |
| helm | v3.16.x | K8s package manager |
| aws-cli v2 | latest | AWS credential management for Bedrock |
| Node.js 22 | v22.x LTS | Admin dashboard dev server |
| Python 3.12 | 3.12.x | Auth gateway local dev (optional, outside cluster) |

---

## 3. Network Topology

### Port Allocation

```
Windows Host (browser access)
  |
  |  :80   --> WSL2 :80  --> k3s ingress-nginx NodePort :80
  |  :443  --> WSL2 :443 --> k3s ingress-nginx NodePort :443
  |  :3000 --> WSL2 :3000 --> admin-dashboard (npm run dev)
  |  :5432 --> WSL2 :5432 --> kubectl port-forward svc/postgres :5432
  |
  +-- (WSL2 auto-forwards ports to Windows host)

k3s Internal Networking (ClusterIP)
  |
  +-- auth-gateway.platform.svc.cluster.local:80 --> Pod :8000
  +-- postgres.database.svc.cluster.local:5432 --> Pod :5432
  +-- claude-terminal-{user}.claude-sessions.svc.cluster.local
  |     :7681 (ttyd)
  |     :8080 (fileserver)
  |     :3000 (user webapp)
  +-- ingress-nginx-controller.ingress-nginx.svc.cluster.local
        :80 (HTTP)
        :443 (HTTPS, self-signed)
```

### Ingress Rules (Local)

```yaml
# Same structure as production, but host changed to claude.local
spec:
  ingressClassName: nginx
  rules:
    - host: claude.local
      http:
        paths:
          # Auth Gateway (login page, API)
          - path: /
            backend: auth-gateway:80

          # Per-user terminal (dynamically created by auth-gateway)
          - path: /terminal/claude-terminal-{user}(/|$)(.*)
            backend: claude-terminal-{user}:7681

          # Per-user files
          - path: /files/claude-terminal-{user}(/|$)(.*)
            backend: claude-terminal-{user}:8080
```

### DNS Setup (Windows hosts file)

```
# Add to C:\Windows\System32\drivers\etc\hosts
127.0.0.1  claude.local
127.0.0.1  claude-admin.local
```

---

## 4. Storage Strategy

### Production vs Local Mapping

| Production | Local Replacement | K8s Resource |
|------------|-------------------|--------------|
| EFS (ReadWriteMany, elastic) | hostPath directory on WSL2 filesystem | PV + PVC using k3s local-path |
| RDS PostgreSQL | PostgreSQL 15 StatefulSet | StatefulSet + PVC |
| ECR (container registry) | Local containerd image store | nerdctl build (no push needed) |

### User Workspace Storage

```
WSL2 Filesystem:
  /opt/bedrock-local/
    +-- workspaces/           <-- replaces EFS
    |     +-- users/
    |     |     +-- n1102359/  <-- per-user workspace
    |     |     +-- testuser/
    |     +-- shared/          <-- shared datasets
    |
    +-- pgdata/               <-- PostgreSQL data
    +-- images/               <-- cached container layers
```

**PersistentVolume definition (local)**:
```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: local-user-workspaces
spec:
  capacity:
    storage: 10Gi
  accessModes:
    - ReadWriteMany          # hostPath supports RWX on single-node
  hostPath:
    path: /opt/bedrock-local/workspaces
    type: DirectoryOrCreate
  storageClassName: local-path
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: efs-shared-pvc       # Same name as production for compatibility
  namespace: claude-sessions
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: local-path
  resources:
    requests:
      storage: 10Gi
```

This design intentionally reuses the PVC name `efs-shared-pvc` so that the auth-gateway `k8s_service.py` Pod creation code works without modification. The Pod template mounts `efs-shared-pvc` and uses `sub_path=users/{username}` exactly as in production.

### PostgreSQL StatefulSet

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: database
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:15-alpine
          ports:
            - containerPort: 5432
          env:
            - name: POSTGRES_DB
              value: bedrock_claude
            - name: POSTGRES_USER
              value: postgres
            - name: POSTGRES_PASSWORD
              value: postgres
          volumeMounts:
            - name: pgdata
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: pgdata
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: local-path
        resources:
          requests:
            storage: 1Gi
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: database
spec:
  selector:
    app: postgres
  ports:
    - port: 5432
      targetPort: 5432
```

---

## 5. AWS Credential Setup for Bedrock Access

Bedrock API access is the one component that **cannot be replicated locally**. Real AWS credentials are required.

### Option A: AWS SSO (Recommended)

```bash
# One-time setup in WSL2
aws configure sso
  # SSO start URL: https://your-sso-portal.awsapps.com/start
  # Region: ap-northeast-2
  # Output: json

# Before each dev session
aws sso login --profile bedrock-dev

# Export for k3s pods
export AWS_PROFILE=bedrock-dev
```

### Option B: IAM User with Static Credentials

```bash
# ~/.aws/credentials (in WSL2)
[bedrock-dev]
aws_access_key_id = AKIA...
aws_secret_access_key = ...
region = us-east-1
```

### Injecting Credentials into Pods

In production, IRSA (IAM Roles for Service Accounts) automatically injects credentials. Locally, there is no IRSA. Two approaches:

**Approach 1 — Mount host AWS credentials into pods (simple, for dev only)**:

The auth-gateway's `k8s_service.py` creates pods with env vars. For local dev, add AWS credential env vars to the pod spec. This requires a small config override in auth-gateway settings:

```env
# auth-gateway .env (local)
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
```

The auth-gateway injects these as env vars into user pods via `_build_env_vars()`. This already happens in the container-image docker-compose.yml pattern.

**Approach 2 — K8s Secret + ServiceAccount (closer to production)**:

```bash
# Create a K8s secret with AWS creds
kubectl create secret generic aws-bedrock-creds \
  -n claude-sessions \
  --from-literal=AWS_ACCESS_KEY_ID=AKIA... \
  --from-literal=AWS_SECRET_ACCESS_KEY=... \
  --from-literal=AWS_REGION=us-east-1
```

Then reference via `envFrom` in the pod template. This requires a minor code path in `k8s_service.py` for local mode.

**Recommendation**: Use Approach 1 for simplicity. The auth-gateway .env file already supports these variables, and the `_build_env_vars()` method can be extended to pass them through.

---

## 6. Auth Gateway Local Configuration

### Environment Differences

```env
# auth-gateway/.env.local (new file for local dev)

# --- App ---
DEBUG=true

# --- Database (local PostgreSQL in k3s) ---
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/bedrock_claude

# --- JWT ---
JWT_SECRET_KEY=dev-secret-key-do-not-use-in-prod

# --- SSO (bypass for local dev) ---
# Option 1: Point to real SSO (if VPN connected)
# SSO_AUTH_URL=https://sso.skons.net/...
# Option 2: Use mock auth (add a /dev/login endpoint)
SSO_AUTH_URL=mock
SSO_CLIENT_ID=dev
SSO_CLIENT_SECRET=dev

# --- Kubernetes ---
K8S_IN_CLUSTER=false
K8S_NAMESPACE=claude-sessions
K8S_POD_IMAGE=localhost/claude-code-terminal:dev
K8S_SERVICE_ACCOUNT=claude-terminal-sa

# --- Bedrock ---
BEDROCK_REGION=us-east-1
BEDROCK_SONNET_MODEL=us.anthropic.claude-sonnet-4-6
BEDROCK_HAIKU_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0

# --- AWS (passed through to user pods) ---
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1

# --- Idle cleanup (longer for dev) ---
IDLE_TIMEOUT_MINUTES=120
IDLE_CHECK_INTERVAL_SECONDS=300

# --- 2FA (disable for local) ---
TWO_FACTOR_ENABLED=false
```

### Hot-Reload Strategy

The auth-gateway can run in two modes locally:

**Mode A: Outside the cluster (recommended for development)**

```bash
# In WSL2, from auth-gateway/ directory
source .venv/bin/activate
cp .env.local .env

# Port-forward PostgreSQL from k3s
kubectl port-forward -n database svc/postgres 5432:5432 &

# Run with hot-reload
uvicorn app.main:app --reload --port 8000 --host 0.0.0.0
```

This gives instant Python hot-reload. The auth-gateway uses `K8S_IN_CLUSTER=false`, so it reads `~/.kube/config` (which k3s auto-provisions at `/etc/rancher/k3s/k3s.yaml`).

**Mode B: Inside the cluster (for integration testing)**

Build and deploy:
```bash
nerdctl build -t localhost/auth-gateway:dev -f auth-gateway/Dockerfile auth-gateway/
kubectl apply -f infra/k8s-local/platform/
```

Mode B does not provide hot-reload but tests the full containerized behavior.

### Admin Dashboard Hot-Reload

```bash
cd admin-dashboard
npm install
# Point API URL to local auth-gateway
NEXT_PUBLIC_API_URL=http://claude.local npm run dev
```

The admin dashboard runs on port 3000 and communicates with auth-gateway at port 8000. The CORS configuration in `main.py` already allows `http://localhost:3000`.

---

## 7. Startup / Teardown Scripts

### `scripts/local-dev/setup.sh` (One-Time Setup)

```bash
#!/bin/bash
set -euo pipefail

echo "=== Bedrock AI Agent — Local Dev Setup (WSL2) ==="

# ---- Prerequisites Check ----
command -v curl >/dev/null || { echo "ERROR: curl required"; exit 1; }

# ---- Install k3s (single-node, no traefik — we use ingress-nginx) ----
if ! command -v k3s &>/dev/null; then
  echo "[1/5] Installing k3s..."
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable traefik" sh -
  # Make kubeconfig accessible without sudo
  sudo chmod 644 /etc/rancher/k3s/k3s.yaml
  mkdir -p ~/.kube
  cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
  echo "k3s installed."
else
  echo "[1/5] k3s already installed, skipping."
fi

# ---- Install nerdctl (Docker-compatible CLI) ----
if ! command -v nerdctl &>/dev/null; then
  echo "[2/5] Installing nerdctl..."
  NERDCTL_VERSION=2.0.3
  wget -q "https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-full-${NERDCTL_VERSION}-linux-amd64.tar.gz"
  sudo tar xzf "nerdctl-full-${NERDCTL_VERSION}-linux-amd64.tar.gz" -C /usr/local
  rm "nerdctl-full-${NERDCTL_VERSION}-linux-amd64.tar.gz"
  echo "nerdctl installed."
else
  echo "[2/5] nerdctl already installed, skipping."
fi

# ---- Install Helm ----
if ! command -v helm &>/dev/null; then
  echo "[3/5] Installing Helm..."
  curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  echo "Helm installed."
else
  echo "[3/5] Helm already installed, skipping."
fi

# ---- Install ingress-nginx ----
echo "[4/5] Installing ingress-nginx..."
helm upgrade --install ingress-nginx ingress-nginx \
  --repo https://kubernetes.github.io/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.service.type=NodePort \
  --set controller.service.nodePorts.http=80 \
  --set controller.service.nodePorts.https=443 \
  --set controller.config.proxy-read-timeout=3600 \
  --set controller.config.proxy-send-timeout=3600 \
  --set controller.config.proxy-body-size=100m \
  --wait

# ---- Create data directories ----
echo "[5/5] Creating local storage directories..."
sudo mkdir -p /opt/bedrock-local/{workspaces/users,workspaces/shared,pgdata}
sudo chown -R $(id -u):$(id -g) /opt/bedrock-local

echo ""
echo "=== Setup complete ==="
echo "Next: run ./scripts/local-dev/start.sh"
```

### `scripts/local-dev/start.sh` (Per-Session Startup)

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
K8S_LOCAL="$PROJECT_ROOT/infra/k8s-local"

echo "=== Starting local dev environment ==="

# ---- Ensure k3s is running ----
if ! sudo k3s kubectl get nodes &>/dev/null; then
  echo "Starting k3s..."
  sudo systemctl start k3s
  sleep 5
fi
echo "[1/6] k3s cluster ready"

# ---- Create namespaces ----
kubectl apply -f "$K8S_LOCAL/namespaces.yaml"
echo "[2/6] Namespaces created"

# ---- Deploy PostgreSQL ----
kubectl apply -f "$K8S_LOCAL/database/"
echo -n "Waiting for PostgreSQL..."
kubectl wait --for=condition=ready pod -l app=postgres -n database --timeout=120s
echo " ready"
echo "[3/6] PostgreSQL running"

# ---- Deploy storage (PV/PVC for workspaces) ----
kubectl apply -f "$K8S_LOCAL/storage/"
echo "[4/6] Storage configured"

# ---- Deploy RBAC ----
kubectl apply -f "$K8S_LOCAL/rbac/"
echo "[5/6] RBAC configured"

# ---- Build container image (if needed) ----
IMAGE_TAG="localhost/claude-code-terminal:dev"
if ! sudo k3s ctr images ls | grep -q "$IMAGE_TAG"; then
  echo "Building claude-code-terminal image (first time, ~5 min)..."
  cd "$PROJECT_ROOT/container-image"
  sudo nerdctl --namespace k8s.io build -t "$IMAGE_TAG" .
  cd "$PROJECT_ROOT"
fi
echo "[6/6] Container image ready"

echo ""
echo "=== Local environment ready ==="
echo ""
echo "Start auth-gateway (hot-reload):"
echo "  kubectl port-forward -n database svc/postgres 5432:5432 &"
echo "  cd auth-gateway && source .venv/bin/activate"
echo "  cp .env.local .env && uvicorn app.main:app --reload --port 8000"
echo ""
echo "Start admin-dashboard (hot-reload):"
echo "  cd admin-dashboard && npm run dev"
echo ""
echo "Access:"
echo "  Auth Gateway:    http://localhost:8000"
echo "  Admin Dashboard: http://localhost:3000"
echo "  PostgreSQL:      localhost:5432 (user: postgres, pass: postgres)"
```

### `scripts/local-dev/teardown.sh` (Cleanup)

```bash
#!/bin/bash
set -euo pipefail

echo "=== Tearing down local dev environment ==="

# Delete user pods first
kubectl delete pods -n claude-sessions -l app=claude-terminal --grace-period=5 2>/dev/null || true

# Delete namespaces (cascade deletes all resources)
kubectl delete namespace claude-sessions --ignore-not-found
kubectl delete namespace database --ignore-not-found

echo "Namespaces deleted. k3s cluster still running."
echo ""
echo "To fully stop k3s: sudo systemctl stop k3s"
echo "To wipe all data:  sudo rm -rf /opt/bedrock-local/"
```

### `scripts/local-dev/rebuild-image.sh` (After container-image changes)

```bash
#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE_TAG="localhost/claude-code-terminal:dev"

echo "Rebuilding claude-code-terminal image..."
cd "$PROJECT_ROOT/container-image"
sudo nerdctl --namespace k8s.io build -t "$IMAGE_TAG" --no-cache .

echo "Done. Restart any running pods to use the new image:"
echo "  kubectl delete pods -n claude-sessions -l app=claude-terminal"
```

---

## 8. Differences from Production

| Aspect | Production (EKS) | Local (k3s on WSL2) | Impact |
|--------|-------------------|---------------------|--------|
| **Node count** | Multi-node (system, user, presenter) | Single-node | No nodeSelector/affinity/taint behavior. All pods land on one node. |
| **AWS auth (IRSA)** | ServiceAccount with IAM role annotation, auto-injected creds | Static AWS creds via env vars or ~/.aws | Pod code is identical. Credential source differs. |
| **Storage (EFS)** | Elastic, multi-AZ, ReadWriteMany | hostPath on WSL2 ext4 | Functionally identical for 1-3 users. No cross-node sharing (irrelevant on single node). |
| **Database (RDS)** | Managed PostgreSQL, multi-AZ, automated backups | PostgreSQL StatefulSet, single-replica, no backups | Schema and queries identical. No HA. |
| **Ingress (NLB + nginx)** | AWS NLB + ingress-nginx with TLS cert | ingress-nginx NodePort, self-signed or no TLS | WebSocket/proxy behavior identical. No valid TLS. |
| **Container registry (ECR)** | Pull from ECR via IRSA | Build locally, image available via shared containerd | No network pull latency. Image must be rebuilt manually. |
| **Cluster Autoscaler** | Scales nodes 0-10 based on pending pods | No scaling (single node, fixed resources) | Not testable locally. |
| **Overprovisioning** | Pause pods reserve capacity for fast startup | Not needed (no scaling) | Not applicable. |
| **Network Policy** | Pod isolation via Calico/Cilium CNI | k3s default CNI (flannel) does not enforce NetworkPolicy | Pod-to-pod isolation NOT enforced locally. Acceptable for dev. |
| **SSO** | sso.skons.net (corporate network) | Requires VPN, or use mock auth endpoint | May need mock login for offline dev. |
| **Replicas** | auth-gateway: 2 replicas | auth-gateway: 1 replica (or outside cluster) | No HA testing. |
| **Pod TTL** | 4h activeDeadlineSeconds | Configurable (default 2h for dev) | Identical mechanism, different default. |
| **Idle cleanup** | 60min WebSocket-based detection | Same code, relaxed timeouts (120min) | Fully testable. |
| **DNS** | claude.skons.net (Route53 + NLB) | claude.local (hosts file) | URL structure identical, domain differs. |
| **TLS** | ACM certificate, HTTPS only | HTTP or self-signed HTTPS | No cert management needed. |
| **Monitoring** | CloudWatch, Prometheus (future) | kubectl logs, local observation | No monitoring stack. |

### What IS Fully Testable Locally

- Pod lifecycle: create, status check, delete, idle cleanup
- Auth flow: SSO login (with VPN) or mock auth
- Terminal access: ttyd WebSocket connection via ingress
- Database operations: all SQLAlchemy models and migrations
- Ingress routing: per-user path-based routing
- Container image changes: rebuild and test
- Admin dashboard: full functionality against local API
- File sharing: EFS-equivalent workspace storage
- Bedrock API: real Claude API calls (with valid AWS creds)

### What CANNOT Be Tested Locally

- Multi-node scheduling (nodeSelector, taints, tolerations, affinity)
- Cluster autoscaling and overprovisioning
- IRSA (IAM Roles for ServiceAccounts)
- NetworkPolicy enforcement (flannel does not support it)
- TLS certificate management
- NLB behavior and health checks
- Cross-AZ storage replication

---

## 9. Resource Requirements

### Minimum (16GB RAM Machine)

| Component | CPU | RAM | Disk |
|-----------|-----|-----|------|
| WSL2 overhead | 0.5 core | 1 GB | — |
| k3s server (kubelet, apiserver, etc.) | 0.5 core | 512 MB | 500 MB |
| ingress-nginx | 0.1 core | 128 MB | — |
| PostgreSQL | 0.1 core | 256 MB | 100 MB |
| auth-gateway (outside cluster) | 0.2 core | 256 MB | — |
| admin-dashboard (npm run dev) | 0.3 core | 512 MB | — |
| **1 user pod (claude-terminal)** | **0.5 core** | **1.5 GB** | 200 MB |
| **Subtotal (1 user)** | **2.2 cores** | **4.2 GB** | ~1 GB |
| **3 user pods** | +1.0 core | +3.0 GB | +400 MB |
| **Total (3 users)** | **3.2 cores** | **7.2 GB** | ~1.5 GB |

### WSL2 Memory Configuration

By default, WSL2 may claim up to 50% of host RAM. For a 16GB machine, configure:

```ini
# %USERPROFILE%\.wslconfig (Windows)
[wsl2]
memory=10GB
swap=4GB
processors=4
```

This leaves 6GB for Windows + browser, which is tight but workable. For comfortable operation with 3 concurrent test pods, 32GB host RAM is recommended.

### Disk Space

| Item | Size |
|------|------|
| k3s binary + runtime | ~300 MB |
| nerdctl-full bundle | ~400 MB |
| claude-code-terminal image | ~2.5 GB |
| auth-gateway image (if containerized) | ~300 MB |
| PostgreSQL data | ~100 MB |
| User workspaces | ~500 MB per user |
| **Total** | **~5 GB minimum** |

---

## 10. Local K8s Manifest Directory Structure

To keep local manifests separate from production, create a parallel directory:

```
infra/
  k8s/                  <-- production (EKS) manifests (unchanged)
  k8s-local/            <-- local dev manifests (new)
    namespaces.yaml
    database/
      postgres.yaml     <-- StatefulSet + Service
    storage/
      pv-workspaces.yaml
      pvc-workspaces.yaml
    rbac/
      rbac.yaml         <-- same ClusterRole, bound to default SA
    platform/
      auth-gateway.yaml <-- Deployment (optional, for Mode B)
      ingress.yaml      <-- host: claude.local
      secrets.yaml      <-- dev secrets (JWT, mock SSO)
```

Production manifests remain untouched. The auth-gateway Python code (`k8s_service.py`) works identically because:
- It reads `K8S_IN_CLUSTER=false` and uses `~/.kube/config`
- It creates pods in `claude-sessions` namespace (same as production)
- It references `efs-shared-pvc` (same PVC name, backed by hostPath locally)
- It sets the image to `K8S_POD_IMAGE` from env (set to `localhost/claude-code-terminal:dev`)

---

## 11. Development Workflow

### Daily Workflow

```
1. Open Windows Terminal (WSL2 tab)
2. Run: ./scripts/local-dev/start.sh          # ~30 seconds if already set up
3. In terminal 1: kubectl port-forward ...     # PostgreSQL
4. In terminal 2: uvicorn app.main:app --reload  # auth-gateway
5. In terminal 3: cd admin-dashboard && npm run dev  # dashboard
6. Open browser: http://localhost:8000         # login page
7. Login -> Pod created -> terminal opens via ingress
8. Develop, test, iterate
9. Ctrl+C terminals when done
```

### After Changing container-image/

```
./scripts/local-dev/rebuild-image.sh
kubectl delete pods -n claude-sessions -l app=claude-terminal
# Next login creates a pod with the new image
```

### After Changing auth-gateway/ Python Code

Automatic (uvicorn --reload watches file changes).

### After Changing admin-dashboard/ React Code

Automatic (Next.js dev server watches file changes).

### After Changing K8s Manifests (infra/k8s-local/)

```
kubectl apply -f infra/k8s-local/<changed-file>.yaml
```

---

## 12. Mock Auth Endpoint (Offline Development)

When corporate VPN is unavailable, SSO authentication cannot reach `sso.skons.net`. For offline development, add a dev-only login bypass:

```python
# auth-gateway/app/routers/auth.py — add at the top of login()
if settings.sso_auth_url == "mock":
    # Dev-only bypass: accept any username with password "dev"
    return create_mock_session(username=request.username)
```

This is controlled by the `SSO_AUTH_URL=mock` setting in `.env.local`. In production, `SSO_AUTH_URL` is always set to the real SSO endpoint, so this code path is never reached.

---

## 13. Comparison with Existing docker-compose.yml

The project already has two docker-compose files:
- `auth-gateway/docker-compose.yml` — runs auth-gateway + PostgreSQL
- `container-image/docker-compose.yml` — runs a single terminal container

These are useful for isolated component testing but cannot replicate the full platform because:

1. **No K8s API**: auth-gateway dynamically creates/deletes pods. docker-compose cannot simulate this.
2. **No ingress routing**: Per-user path-based routing (`/terminal/{pod-name}/`) requires an ingress controller.
3. **No pod lifecycle**: The idle cleanup service, pod status checks, and session tracking all depend on the K8s API.
4. **No multi-pod**: docker-compose runs a fixed set of containers. The platform creates pods on-demand per user.

The k3s-based local environment solves all of these while remaining lightweight enough for a 16GB machine.

The existing docker-compose files remain useful for:
- Quick auth-gateway API testing (no K8s needed)
- Container image iteration (build and run a single terminal)
- CI pipeline unit tests

---

## 14. Troubleshooting Guide

### k3s fails to start in WSL2

```bash
# Check if systemd is enabled (required for k3s service)
cat /etc/wsl.conf
# Should contain:
# [boot]
# systemd=true

# If not, add it and restart WSL: wsl --shutdown (from PowerShell)
```

### nerdctl build fails with "buildkitd not running"

```bash
# Start buildkitd manually
sudo buildkitd &
# Or use k3s containerd directly
sudo nerdctl --namespace k8s.io --address /run/k3s/containerd/containerd.sock build ...
```

### Pods cannot pull image "localhost/claude-code-terminal:dev"

```bash
# Images must be built into k3s containerd namespace
sudo nerdctl --namespace k8s.io build -t localhost/claude-code-terminal:dev .
# Verify
sudo k3s ctr images ls | grep claude
```

### Port 80 already in use

```bash
# Check what is using port 80
sudo lsof -i :80
# If it is another web server, stop it or change ingress-nginx NodePort:
helm upgrade ingress-nginx ... --set controller.service.nodePorts.http=8080
```

### kubectl cannot connect to cluster

```bash
# k3s kubeconfig location
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
# Or copy to default location
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
```

### Windows browser cannot reach WSL2 services

```bash
# Check WSL2 IP
hostname -I
# If not 127.0.0.1, WSL2 may be using NAT mode
# Ensure "networkingMode=mirrored" in .wslconfig (Windows 11 22H2+):
# [wsl2]
# networkingMode=mirrored
```
