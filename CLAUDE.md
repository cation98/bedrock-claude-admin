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

## Design Document

전체 시스템 설계: `docs/plans/2026-03-21-bedrock-claude-code-platform-design.md`
