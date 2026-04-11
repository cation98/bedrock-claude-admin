# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AWS Bedrock 기반 사내 Claude Code 활용 플랫폼. 임원/팀장 실습 세션과 실무자 일상 개발 지원을 위한 Internal Developer Platform.

**핵심 목적**: 사내 SSO(sso.skons.net) 인증 → AWS Bedrock Claude API → 사용자별 격리된 웹 터미널(K8s Pod) 제공

## Architecture

```
Users (CLI / Web Terminal)
    → Auth Gateway (FastAPI) — SSO 인증, AWS STS 자격증명 발급, Pod 관리
    → EKS Cluster — 사용자별 Pod (Claude Code + ttyd + psql)
    → Admin Dashboard (Next.js) — 세션/사용량 모니터링
    → AWS Bedrock — Claude Sonnet 4.6 / Haiku 4.5
    → RDS ReadOnly Replica (safety-prod) — 실습/실무 데이터 접근
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
- Phase 1 MVP (임원 15명 실습, 1주 내) → Phase 2 (팀장 50명) → 상시 운영 (실무자 10명) 순서로 확장

## Infrastructure Design Constraints

### System Node Pair 운용 (필수)

시스템 노드 2대에 auth-gateway + ingress-nginx를 **pair로 배치**한다. 이것은 본 프로젝트의 기본 설계이며 반드시 지켜야 한다.

```
System Node A: auth-gateway replica-1 + ingress-nginx replica-1
System Node B: auth-gateway replica-2 + ingress-nginx replica-2
```

**구현 방법:**
- auth-gateway: `requiredDuringSchedulingIgnoredDuringExecution` anti-affinity (hard) — 동일 노드 배치 금지
- auth-gateway: `preferredDuringSchedulingIgnoredDuringExecution` pod affinity — ingress-nginx 근접 선호
- ingress-nginx: `requiredDuringSchedulingIgnoredDuringExecution` anti-affinity (hard) — 동일 노드 배치 금지
- system nodegroup: `system-node-large` (t3.large), desired=2, max=3

**주의:**
- 이미지 배포(rollout restart) 시 일시적으로 pair가 깨질 수 있으나, 안정 운용 시 반드시 pair 상태를 유지해야 함
- 시스템 노드를 3대 이상으로 늘리지 않음 (비용 최적화)
- anti-affinity를 soft(preferred)로 변경하지 않음 — 동일 노드 몰림 방지가 목적

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
