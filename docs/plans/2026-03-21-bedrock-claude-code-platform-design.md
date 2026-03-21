# Bedrock Claude Code Platform — System Design

**Date**: 2026-03-21
**Status**: Approved
**Author**: Claude Code + Developer

---

## 1. Overview

AWS Bedrock 기반 사내 Claude Code 활용 플랫폼. 임원/팀장 실습 세션과 실무자 일상 개발 지원을 위한 Full Internal Developer Platform.

### Rollout Phases

| Phase | 대상 | 동시 사용자 | 환경 | RDS | 일정 |
|-------|------|-----------|------|-----|------|
| Phase 1 | 임원 15명 | 15명 | Web Terminal (K8s Pod) | safety-prod ReadOnly Replica | 1주 내 |
| Phase 2 | 팀장 50명 | 50명 | Web Terminal (K8s Pod) | 별도 RDS 추가 | TBD |
| 상시 | 실무자 ~10명 | ~10명 | Local CLI + Web Terminal | safety-prod ReadOnly Replica | Phase 1 이후 |

---

## 2. Architecture

```
                    ┌─────────────────────┐
                    │   사용자 접근 레이어    │
                    ├──────────┬──────────┤
                    │ CLI 사용자 │ Web 사용자 │
                    │ (실무자)   │ (임원/팀장) │
                    └────┬─────┴─────┬────┘
                         │           │
                         ▼           ▼
              ┌──────────────────────────────┐
              │     Ingress (NGINX/ALB)       │
              │     *.claude.skons.net        │
              └──────────────┬───────────────┘
                             │
           ┌─────────────────┼─────────────────┐
           ▼                 ▼                  ▼
  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │ Auth Gateway │  │ Admin        │  │ Terminal     │
  │ (FastAPI)    │  │ Dashboard    │  │ Proxy        │
  │              │  │ (Next.js)    │  │ (per-user    │
  │ - SSO 인증    │  │ - 사용량 모니터│  │  routing)    │
  │ - AWS STS    │  │ - 세션 관리    │  │              │
  │ - 세션 관리    │  │ - 사용자 관리  │  │              │
  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
         │                 │                  │
         ▼                 ▼                  ▼
  ┌─────────────────────────────────────────────────┐
  │              EKS Cluster (Private VPC)            │
  │                                                   │
  │  ┌─────────┐ ┌─────────┐ ┌─────────┐            │
  │  │ Pod:    │ │ Pod:    │ │ Pod:    │  ...        │
  │  │ user-01 │ │ user-02 │ │ user-N  │            │
  │  │         │ │         │ │         │            │
  │  │ Claude  │ │ Claude  │ │ Claude  │            │
  │  │ Code    │ │ Code    │ │ Code    │            │
  │  │ + ttyd  │ │ + ttyd  │ │ + ttyd  │            │
  │  │ + psql  │ │ + psql  │ │ + psql  │            │
  │  └────┬────┘ └────┬────┘ └────┬────┘            │
  │       │           │           │                   │
  │       └───────────┼───────────┘                   │
  │                   │                               │
  └───────────────────┼───────────────────────────────┘
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
  ┌────────────┐ ┌──────────┐ ┌──────────┐
  │ RDS        │ │ AWS      │ │ Platform │
  │ ReadOnly   │ │ Bedrock  │ │ DB       │
  │ Replica    │ │ Claude   │ │ (RDS/    │
  │ (safety-   │ │ API      │ │  PG)     │
  │  prod)     │ │          │ │          │
  └────────────┘ └──────────┘ └──────────┘
```

---

## 3. Component Details

### 3.1 Auth Gateway (FastAPI)

사내 SSO(sso.skons.net)를 통해 인증하고, 인증된 사용자에게 AWS 임시 자격증명을 발급.

**Endpoints:**
- `POST /api/v1/auth/login` — SSO 인증 (username + password → sso.skons.net → JWT 발급)
- `POST /api/v1/auth/logout` — 세션 종료 + Pod 정리
- `GET /api/v1/auth/me` — 현재 사용자 정보
- `POST /api/v1/sessions/start` — K8s Pod 생성 + 웹 터미널 세션 시작
- `DELETE /api/v1/sessions/{id}` — Pod 삭제 + 세션 종료
- `GET /api/v1/sessions/` — 활성 세션 목록

**Authentication Flow:**
```
1. Client → Auth Gateway: POST /login {username, password}
2. Auth Gateway → sso.skons.net/auth: SSO 인증 (SHA-256 + Salt)
3. sso.skons.net → Auth Gateway: access_token
4. Auth Gateway → sso.skons.net/userinfo: 사용자 정보 조회
5. Auth Gateway: JWT 발급 + DB에 사용자 등록/업데이트
6. Auth Gateway → K8s API: Pod 생성 (Claude Code + ttyd + AWS 자격증명 주입)
7. Client: 웹 터미널 접속 URL 반환
```

**SSO Integration (from O-Guard pattern):**
- `SSO_AUTH_URL`: 인증 엔드포인트
- `SSO_AUTH_URL2`: 사용자 정보 조회 엔드포인트
- Password encoding: `SHA-256(password + salt) → Base64`
- Client ID/Secret 기반 인증

### 3.2 Container Image (claude-code-terminal)

각 사용자에게 제공되는 격리된 실습 환경.

**Dockerfile 구성:**
```
Base: node:22-bookworm-slim
+ Claude Code CLI (@anthropic-ai/claude-code)
+ ttyd (웹 터미널 서버)
+ PostgreSQL client (psql)
+ git, vim, Python 3.x
+ AWS CLI v2
+ 샘플 프로젝트 코드 (pre-clone)
```

**환경변수 (Pod 생성 시 주입):**
```bash
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=us-east-1  # or ap-northeast-2
AWS_ACCESS_KEY_ID=<STS 임시 자격증명>
AWS_SECRET_ACCESS_KEY=<STS 임시 자격증명>
AWS_SESSION_TOKEN=<STS 임시 자격증명>
ANTHROPIC_DEFAULT_SONNET_MODEL=us.anthropic.claude-sonnet-4-6
ANTHROPIC_DEFAULT_HAIKU_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0
```

**ttyd 설정:**
- Port: 7681
- 읽기 전용 모드 OFF
- 인증: Auth Gateway JWT를 통한 프록시 인증

### 3.3 EKS Cluster

**Cluster 구성:**
- Node Group: `m5.large` × 2~4 (auto-scaling)
- Namespace: `claude-sessions` (사용자 Pod), `platform` (Auth Gateway, Admin)
- Pod Resource Limits: CPU 0.5 core, Memory 512Mi per user pod
- Pod Lifecycle: 세션 시작 시 생성, 종료 시 삭제 (최대 TTL 4시간)

**Scaling 전략:**
| Phase | 동시 Pod | Node 수 | Instance |
|-------|---------|---------|----------|
| Phase 1 (15명) | 15 | 2× m5.large | 2 vCPU, 8GB |
| Phase 2 (50명) | 50 | 4× m5.xlarge | 4 vCPU, 16GB |
| 상시 (10명) | 10 | 2× m5.large | Auto-scale |

### 3.4 Admin Dashboard (Next.js)

**주요 기능:**
- 실시간 활성 세션 모니터링 (누가 접속 중인지)
- 사용자별/팀별 Bedrock 토큰 사용량 조회
- 실습 세션 관리 (일괄 생성/종료)
- 사용자 권한 관리 (admin, user, viewer)
- 비용 추정 대시보드

### 3.5 Platform Database (PostgreSQL)

**Tables:**
- `users` — 사번, 이름, 역할, 소속, SSO 연동 정보
- `sessions` — 세션 ID, 사용자, Pod 이름, 시작/종료 시각, 상태
- `usage_logs` — 요청 시각, 모델, input/output tokens, 비용 추정
- `workshop_events` — 실습 이벤트 (Phase 1, Phase 2 등) 관리

---

## 4. Infrastructure (Terraform)

### AWS Resources:
- **EKS Cluster** + Managed Node Groups
- **ECR** — Container image registry
- **RDS PostgreSQL** — Platform DB (별도, safety-prod와 분리)
- **ALB** — Ingress load balancer
- **IAM Roles** — Bedrock 접근, EKS 노드, STS AssumeRole
- **VPC** — Private subnets (기존 VPC 활용 또는 신규)
- **Secrets Manager** — SSO credentials, DB passwords
- **CloudWatch** — 로깅 및 모니터링

### IAM Policy (Bedrock 접근용):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
        "arn:aws:bedrock:*::inference-profile/us.anthropic.claude-*"
      ]
    }
  ]
}
```

---

## 5. Security Considerations

- **네트워크 격리**: EKS + RDS 모두 Private VPC 내 운영
- **임시 자격증명**: STS AssumeRole로 단기 AWS 자격증명 (1~4시간)
- **RDS 읽기 전용**: ReadOnly Replica로 데이터 변조 방지
- **Pod 격리**: 사용자별 독립 Pod, NetworkPolicy로 Pod 간 통신 차단
- **세션 TTL**: 최대 4시간, 자동 종료 및 Pod 삭제
- **Guardrails**: Bedrock Guardrails로 민감 정보 필터링 가능 (Phase 2+)

---

## 6. Phase 1 MVP Scope (1주 내)

**포함:**
- [x] Auth Gateway: SSO 로그인 → Pod 생성 → 웹 터미널 URL 반환
- [x] Container Image: Claude Code + ttyd + psql
- [x] EKS Cluster: 15 Pod 동시 운용
- [x] 기본 Admin: 세션 목록 조회, 일괄 생성/종료
- [x] Bedrock 연동: Sonnet 4.6 모델 사용

**제외 (Phase 2+):**
- 상세 사용량 대시보드
- 비용 관리 / 할당량
- Guardrails 정책 관리
- 자동 프로비저닝

---

## 7. Tech Stack Summary

| Component | Technology | Location |
|-----------|-----------|----------|
| Auth Gateway | FastAPI (Python 3.12) | `auth-gateway/` |
| Admin Dashboard | Next.js 15 + React | `admin-dashboard/` |
| Container Image | Docker (node:22-bookworm-slim) | `container-image/` |
| K8s Manifests | YAML + Helm (optional) | `infra/k8s/` |
| Infrastructure | Terraform | `infra/terraform/` |
| Scripts | Bash/Python | `scripts/` |
| CI/CD | GitHub Actions | `.github/workflows/` |
