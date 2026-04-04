# Session: 2026-04-04 — 사용자별 Telegram 봇 + 외부 API 화이트리스트

## Delivered
- Design Doc APPROVED (3-round adversarial review, 4→6→7/10)
- 경로: `~/.gstack/projects/cation98-bedrock-ai-agent/cation98-main-design-20260404-120435.md`

## Architecture Decisions

### 사용자별 Telegram 봇
- user_bots 테이블 (deployed_apps와 분리)
- 웹훅: auth-gateway `/api/v1/telegram/bot/{bot_token_hash}/webhook` → Pod IP:8080
- 토큰 보안: AWS KMS envelope encryption, SHA-256 hash 라우팅
- Pod Contract: 포트 8080, POST /bot/webhook (언어 무관)
- 핵심: 봇 = App Factory의 새 배포 타겟

### 외부 API 화이트리스트
- 커스텀 asyncio CONNECT proxy (포트 3128, ~80줄)
- 도메인 단위만 (CONNECT는 path 불가), 와일드카드 suffix matching
- 인증: USER_ID + POD_PROXY_SECRET (Basic auth)
- HTTPS_PROXY env var로 DX 확보
- 배포순서: 프록시 배포 → env var → NetworkPolicy tighten → Pod 재시작

### NetworkPolicy 변경
- Ingress: platform→Pod 8080 허용 추가
- Egress: 0.0.0.0/0:443 제거 → 프록시(3128) 경유 필수
- 필수 도메인: apis.data.go.kr, *.amazonaws.com, api.telegram.org, npm/pip, github

### Review 핵심 발견
- CLAUDE_TOKEN은 Pod에 미존재 → USER_ID + POD_PROXY_SECRET로 해결
- HTTP CONNECT는 path 필터링 불가 → domain-only
- NetworkPolicy가 platform→Pod 8080 차단 → ingress 추가
- K8s Service 3128 포트 + TerminalSession.proxy_secret 컬럼 추가 필요

## Git
- 5e489c7: admin dashboard rebuild + Serena memories + gitignore

## Deferred
- 메시지 큐, per-user 화이트리스트, AI agent 봇, rate limiting, path 필터링
