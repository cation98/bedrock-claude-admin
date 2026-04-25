# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AWS Bedrock 기반 사내 AI 에이전트 플랫폼. 전 직원이 Claude Code를 일상 업무에 활용할 수 있도록 SSO 인증, 격리된 실행 환경, 사용량 관리를 제공하는 Internal AI Platform.

**핵심 목적**: 사내 SSO(sso.skons.net) 인증 → AWS Bedrock Claude API → 사용자별 격리된 웹 터미널(K8s Pod) 제공

## Architecture

```
Users (CLI / Web Terminal)
    → Auth Gateway (FastAPI) — SSO 인증, AWS STS 자격증명 발급, Pod 관리
    → EKS Cluster — 사용자별 Pod (Claude Code + ttyd + psql)
    → Admin Dashboard (Next.js) — 세션/사용량 모니터링
    → AWS Bedrock — Claude Sonnet 4.6 / Haiku 4.5
    → RDS ReadOnly Replica (safety-prod) — 실무 데이터 접근
```

### Components

| Component | Stack | Directory | Port |
|-----------|-------|-----------|------|
| Auth Gateway | FastAPI + Python 3.12 | `auth-gateway/` | 8000 |
| Admin Dashboard | Next.js 15 + React | `admin-dashboard/` | 3000 |
| Container Image | Docker (node:22 + Claude Code + ttyd) | `container-image/` | 7681 (ttyd) |
| K8s Manifests | YAML | `infra/k8s/` | — |
| Terraform IaC | HCL | `infra/terraform/` | — |
| Utility Scripts | Bash/Python | `scripts/` | — |

## Build & Run Commands

### Auth Gateway
```bash
cd auth-gateway
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Admin Dashboard
```bash
cd admin-dashboard
npm install
npm run dev          # development
npm run build        # production build
```

### Container Image
```bash
cd container-image
docker build -t claude-code-terminal .
docker run -p 7681:7681 --env-file .env claude-code-terminal
```

### Infrastructure
```bash
cd infra/terraform
terraform init
terraform plan
terraform apply

# EKS kubeconfig
aws eks update-kubeconfig --name bedrock-claude-cluster --region ap-northeast-2

# K8s manifests
kubectl apply -f ../k8s/namespace.yaml
kubectl apply -f ../k8s/
```

## Key Environment Variables

```bash
# Bedrock (injected into user Pods)
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=us-east-1
ANTHROPIC_DEFAULT_SONNET_MODEL=us.anthropic.claude-sonnet-4-6
ANTHROPIC_DEFAULT_HAIKU_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0

# Auth Gateway
SSO_AUTH_URL=         # sso.skons.net auth endpoint
SSO_AUTH_URL2=        # sso.skons.net userinfo endpoint
SSO_CLIENT_ID=        # from AWS Secrets Manager
SSO_CLIENT_SECRET=    # from AWS Secrets Manager
PW_ENCODING_SALT=     # SHA-256 password encoding salt
DATABASE_URL=         # Platform PostgreSQL
```

## SSO Authentication

사내 SSO는 custom JSON API (not OIDC/SAML). O-Guard 프로젝트(`/Users/cation98/Project/O-Guard/o-guard-api-server`)의 인증 패턴을 재사용:
- Password encoding: `SHA-256(password + salt) → Base64`
- Two-endpoint flow: AUTH_URL(토큰 발급) → AUTH_URL2(사용자 정보)
- SSO credentials는 AWS Secrets Manager에서 로드

## Developer Context

- 개발자가 Kubernetes를 이 프로젝트를 통해 학습 중 — K8s 관련 코드/설정에는 상세한 주석과 설명을 포함할 것
- O-Guard 프로젝트의 FastAPI 패턴(config loading, auth dependencies, error handling)을 참고할 것
- 사내 전 직원 대상 상시 운영 AI 에이전트 플랫폼 — 단계적 확장(사용자 수, 기능) 진행 중

## Infrastructure Design Constraints

### System Node 운용 (필수)

auth-gateway와 ingress-nginx는 **별도 nodegroup으로 분리**하여 운용한다. 이것은 Phase 0 engineering review 후 확정된 설계이며 반드시 지켜야 한다.

```
[system-node-large] Node A: auth-gateway replica-1
[system-node-large] Node B: auth-gateway replica-2

[ingress-workers] Node 1~N (min 2 / max 6): ingress-nginx
```

#### auth-gateway (system-node-large nodegroup)

- nodegroup: `system-node-large` (t3.large), desired=2, max=3
- anti-affinity: `requiredDuringSchedulingIgnoredDuringExecution` (hard) — 동일 노드 배치 금지
- nodeSelector: `role: system` + toleration `dedicated=system:NoSchedule`

#### ingress-nginx (ingress-workers nodegroup) — Phase 0 신설

- nodegroup: `ingress-workers` (t3.large), min=2, max=6
- Open WebUI WebSocket 트래픽 대응을 위해 최대 6노드까지 확장 허용
- anti-affinity: `requiredDuringSchedulingIgnoredDuringExecution` (hard) — 동일 노드 배치 금지
- nodeSelector: `role: ingress` + toleration `dedicated=ingress:NoSchedule`

**주의:**
- auth-gateway와 ingress-nginx 간 pod affinity(근접 선호)는 제거됨 — 서로 다른 nodegroup에 위치
- auth-gateway anti-affinity는 soft(preferred)로 변경 금지 — 동일 노드 몰림 방지가 목적
- 이미지 배포(rollout restart) 시 일시적으로 anti-affinity가 깨질 수 있으나 안정 운용 시 각 노드에 분산 유지

## Design System
Always read DESIGN.md before making any visual or UI decisions.
All font choices, colors, spacing, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag any code that doesn't match DESIGN.md.

## Design Document

전체 시스템 설계: `docs/plans/2026-03-21-bedrock-claude-code-platform-design.md`

## mindbase 컨텍스트 로드

실질적 개발 작업(구현, 버그 수정, 리팩터링, 인프라 변경) 시작 시:
1. `mcp__mindbase__memory_list(project="bedrock-ai-agent")`로 기존 지식 목록 확인
2. 현재 작업과 관련된 memory가 있으면 `memory_read`로 로드하여 참고
3. 전체를 로드하지 않고, 작업 맥락에 맞는 것만 선택적으로 읽을 것

## 디버깅 기록 보존

`/investigate` 등 디버깅 완료 후 DEBUG REPORT를 작성하면:
- `mcp__mindbase__memory_write(project="bedrock-ai-agent", category="pattern")`으로 교훈을 저장
- name: `debug-{간결한-키워드}` 형식
- tags: 관련 컴포넌트, 에러 유형 포함
- 내용은 Symptom, Root Cause, Fix, 교훈을 압축하여 기록 (전체 대화가 아닌 핵심만)

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
