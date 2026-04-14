# Plugin System Redesign — Gitea 기반 사내 Mirror + Curated Pre-bake

**작성일**: 2026-04-15
**상태**: 설계 승인 완료 (브레인스토밍 종료)
**대상 Phase**: 실무자 상시 운영 (MVP 이후 확장 단계)

## 배경

AWS Bedrock 기반 사내 Claude Code 플랫폼은 User Pod(K8s)에서 Claude Code + ttyd를 실행한다. 현재 플러그인 시스템은 다음 문제를 겪고 있다:

1. **Pre-baked 플러그인이 동작하지 않음**: `plugins-config.json`이 `{"repositories": {}}` 상태여서 enabled 플래그가 설정되지 않아 Claude Code가 플러그인을 로드하지 않음. Installed 탭에 `superpowers`가 보이지 않고 `serena`만 `failed` 표시.
2. **Marketplace 불일치**: `known-marketplaces.json`에 7개 marketplace가 등록되어 있지만 실제 번들된 건 `superpowers-marketplace` 하나뿐. 나머지 6개를 Pod 기동 시 GitHub에서 clone 시도 → 네트워크 차단으로 대량 에러.
3. **serena 실행 의존성 누락**: `installed_plugins.json` 버전이 `unknown`, 이미지에 Python/uvx 런타임 없음. MCP 서버 기동 실패.
4. **버전 drift**: Dockerfile에 `superpowers/4.0.3` 하드코딩, 호스트 캐시는 `5.0.7`. 이미지 재빌드 없이 최신 반영 불가.
5. **코드 유출 방지 수단 부재**: 사용자가 실습/실무 코드를 public GitHub에 push할 수 있음. 사내 git server 미존재.

## 목표

- **Plugin 시스템 정상 동작**: 3개 curated 플러그인(superpowers, frontend-design, feature-dev)을 pre-bake + 자동 enabled 상태로 제공
- **런타임 확장성**: 사용자가 `/plugin` UI에서 사내 mirror 기반으로 추가 플러그인 설치 가능
- **사용자 코드 외부 유출 차단**: Public GitHub push 기술적 차단 + 사내 Gitea로 push 강제
- **git MCP 사용 지원**: Claude Code가 git 작업을 MCP로 수행 가능 (사내 Gitea 대상)
- **Anthropic marketplace 자동 최신화**: 주기적 자동 sync로 신규 플러그인 자동 반영

## 비목표 (Non-Goals)

- Public GitHub에 대한 read 접근 제공 (완전 차단)
- serena 플러그인 복구 (Phase 2 사이드카 방식으로 별도 설계 예정)
- 사내 GitLab 전면 구축 (Gitea로 충분, CI 수요는 별도 검토)
- 플러그인 관리자 수동 검토 프로세스 (자동 sync로 결정)

## 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────┐
│ EKS Cluster (VPC, egress: github.com 차단)                   │
│                                                              │
│  ┌─────────────┐  ┌─────────────────────────────────────┐  │
│  │ User Pod    │  │ Gitea (neo namespace)               │  │
│  │             │  │                                     │  │
│  │ Claude Code │──┼─▶ /mirrors/*  (plugin marketplace)  │  │
│  │  + git MCP  │  │   - superpowers-marketplace         │  │
│  │  + ttyd     │  │   - claude-plugins-official         │  │
│  │             │──┼─▶ /users/<sso-id>/*  (개인 레포)    │  │
│  │             │  │                                     │  │
│  │ NetworkPolicy│ │ RDS Postgres (별도 인스턴스)        │  │
│  │  egress:    │  │                                     │  │
│  │  - Gitea    │  │ 자동 mirror (Gitea 내장, 6시간)     │  │
│  │  - Bedrock  │  └─────────────────────────────────────┘  │
│  │  - RDS RO   │                                            │
│  │  - SSO      │       (github.com egress: Gitea만 허용)    │
│  └─────────────┘                                            │
└─────────────────────────────────────────────────────────────┘
```

**핵심 원칙:**
- **Gitea가 단일 git gateway** — User Pod에서 도달 가능한 유일한 git 서버. Plugin mirror + 사용자 코드 저장소 겸용.
- **이미지 빌드 시점에 3개 플러그인 pre-bake** — superpowers / frontend-design / feature-dev. enabled 상태로 자동 주입.
- **런타임 추가 설치는 Gitea mirror에서만** — `known-marketplaces.json`이 Gitea URL만 가리킴.
- **github.com outbound 차단 (User Pod)** — EKS NetworkPolicy로 강제. Gitea CronJob만 예외.
- **Anthropic marketplace 자동 sync** — Gitea 내장 mirror 기능이 6시간마다 자동 pull.

## 컴포넌트 설계

### 1. Gitea 배포

| 항목 | 값 |
|---|---|
| Helm chart | `gitea-charts/gitea` |
| 네임스페이스 | `gitea` (신규) |
| Node 배치 | `system-node-large` nodegroup (auth-gateway와 동일 pair) |
| Replica | 2 (HA) |
| DB | RDS PostgreSQL 별도 인스턴스 (stateful-set 내장 DB 금지) |
| Storage | EBS PVC 500Gi (gp3, 확장 가능) |
| Auth | SSO(sso.skons.net) OAuth2 연동 — auth-gateway가 사용하는 동일 플로우 |
| Ingress | 내부 전용 `gitea.internal.skons.net` (인터넷 노출 X) |
| 백업 | RDS snapshot 일 1회, 30일 retention |
| 가용성 타겟 | 99% (월 7.2시간 허용) |
| LFS | 활성화 (실무 코드 대용량 파일 대비) |

### 2. Plugin Mirror

**Sync 메커니즘**: Gitea 내장 **Mirror Repository** 기능. 관리자가 Gitea에서 mirror 레포를 1회 생성하면 Gitea가 스스로 주기적으로 `git fetch` 수행.

**Sync 주기**: 6시간 (Gitea 기본값 8h에서 단축. 각 mirror 레포 생성 시 `mirror_interval=6h0m0s` 명시)

**Sync 대상 (초기)**:

| Marketplace | GitHub Source | Gitea Destination |
|---|---|---|
| claude-plugins-official | `anthropics/claude-plugins-official` | `gitea.internal.skons.net/mirrors/claude-plugins-official` |
| superpowers-marketplace | `obra/superpowers-marketplace` | `gitea.internal.skons.net/mirrors/superpowers-marketplace` |

**Sync 승인 정책**: 자동. 새 플러그인이 Anthropic marketplace에 추가되면 자동으로 사용자에게 노출.

**모니터링 CronJob**: Gitea API로 각 mirror의 last-sync 시각 조회. 24h 이상 실패 시 Slack 알림.

### 3. User Pod — Dockerfile 변경

**Pre-bake 대상 플러그인** (3개):

| 플러그인 | 소스 | 배치 경로 |
|---|---|---|
| superpowers | claude-plugins-official (최신 안정 버전) | `/home/node/.claude/plugins/cache/claude-plugins-official/superpowers/<version>/` |
| frontend-design | claude-plugins-official | `/home/node/.claude/plugins/cache/claude-plugins-official/frontend-design/<version>/` |
| feature-dev | claude-plugins-official | `/home/node/.claude/plugins/cache/claude-plugins-official/feature-dev/<version>/` |

**수정/제거 대상 설정 파일**:

| 파일 | 변경 내용 |
|---|---|
| `installed_plugins.json` | 3개 플러그인 등록 (정확한 버전 명시, serena 제거) |
| `plugins-config.json` | `repositories.<marketplace>.enabled = true` 플래그 설정 — 현재 빈 객체 문제 해결 |
| `known-marketplaces.json` | Gitea URL만 나열 (`source.url = https://gitea.internal.../mirrors/...`), 기존 GitHub 참조 전부 제거 |
| `plugins-marketplaces/` 디렉토리 | 삭제 (Gitea가 담당) |
| `plugins-blocklist.json` | 빈 배열로 초기화 (테스트 데이터 제거) |

**버전 관리**: Dockerfile 상단에 `ARG SUPERPOWERS_VERSION=x.y.z` 형태로 명시. 이미지 빌드 시 CI가 plugin manifest lint 수행.

### 4. User Pod — entrypoint.sh 변경

신규 Pod 기동 시 다음을 수행:

1. **Gitea 개인 저장소 확인/생성**: auth-gateway가 spawn 시점에 이미 처리했으므로 Pod에서는 토큰 확인만.
2. **`~/.gitconfig` 생성**:
   - `[url "https://gitea.internal.skons.net/mirrors/"]` `insteadOf = https://github.com/` — github.com 참조를 Gitea mirror로 리다이렉트
   - `[credential "https://gitea.internal.skons.net"]` `helper = store` — 세션 토큰 자동 인증
   - `[user]` `name`, `email` — SSO 정보 주입
3. **Git credential 파일 생성**: `~/.git-credentials`에 Gitea 토큰 저장 (파일 권한 600)
4. **pre-push hook 설치 (글로벌)**: `/usr/local/share/git-hooks/pre-push` — remote URL이 Gitea가 아니면 거부. `git config --global core.hooksPath` 설정.

### 5. Auth Gateway 변경

사용자 SSO 로그인 처리 시 추가 단계:

1. Gitea admin API로 사용자 존재 확인 (`GET /api/v1/admin/users/<sso-id>`)
2. 없으면 생성 (`POST /api/v1/admin/users`) — SSO ID = Gitea username, 이메일 동기화
3. 사용자별 access token 발급 (`POST /api/v1/users/<user>/tokens`) — scope: `write:repository`, 24h 만료
4. Pod spawn 시 env 주입:
   - `GITEA_URL=https://gitea.internal.skons.net`
   - `GITEA_USER=<sso-id>`
   - `GITEA_TOKEN=<session-token>`

실패 시 3회 재시도 → 그래도 실패하면 사용자에게 "잠시 후 재로그인" 안내 + 관리자 Slack 알림.

### 6. NetworkPolicy

**User Pod egress** (`neo` 또는 `users` namespace):

허용:
- `gitea` namespace (TCP 80/443)
- Bedrock API 엔드포인트 (AWS VPC endpoint)
- RDS read-only replica (TCP 5432)
- SSO 엔드포인트 (`sso.skons.net`)
- `auth-gateway` service
- DNS (kube-dns)

거부: 그 외 모든 outbound (github.com 포함 인터넷 전체)

**Gitea Mirror egress** (`gitea` namespace):

허용:
- `github.com:443` (read 전용, mirror fetch 용도)
- RDS Postgres
- Internal services

이 namespace는 User Pod에서 접근 불가한 전용 영역.

### 7. Phase 2 확장 여지 (설계 범위 외)

- **serena MCP 사이드카**: User Pod과 분리된 Pod에 serena를 배포, MCP over network(gRPC/HTTP)로 Claude Code와 연결. Python/uvx 의존성을 User Pod에서 제거.
- **Custom internal marketplace**: 사내에서 개발한 skill을 Gitea 내 별도 marketplace 레포로 운영. `known-marketplaces.json`에 Gitea URL 추가.
- **CI 수요 발생 시 GitLab 전환 검토**.

## 데이터 흐름

### 흐름 1: 사용자 첫 로그인 → Pod 기동

```
User → auth-gateway (SSO)
  → Gitea 사용자 프로비저닝 (없으면 생성)
  → Gitea access token 발급 (24h)
  → User Pod spawn (GITEA_* env 주입)
  → entrypoint.sh:
      - ~/.gitconfig 생성 (insteadOf + credential)
      - pre-push hook 설정
      - ttyd 기동
```

### 흐름 2: Claude Code 플러그인 로드

```
Claude Code 시작
  → installed_plugins.json (3개 pre-baked)
  → plugins-config.json (enabled=true)
  → /home/node/.claude/plugins/cache/.../ 에서 skills/commands/hooks 로드
  → /plugin UI에 3개 Installed + enabled 표시

사용자가 /plugin으로 추가 설치:
  → known-marketplaces.json의 Gitea URL 조회
  → Gitea에서 marketplace 레포 clone
  → 사용자별 ~/.claude/plugins/cache/ 에 저장 (Pod 휘발 시 사라짐)
```

### 흐름 3: 사용자 코드 작업 → Gitea push

```
Claude Code + git MCP:
  git clone https://github.com/org/repo
    → insteadOf 규칙으로 Gitea mirror 시도
    → mirror에 없으면 실패 → 사용자가 Gitea에서 직접 import/fork
  git commit ...
  git push origin main
    → remote URL = gitea.internal... → 정상 push
    → pre-push hook: remote 검증 → Gitea 외면 거부
    → NetworkPolicy: 외부 egress 차단 (2차 방어)
```

### 흐름 4: Plugin Mirror Sync

```
Gitea 내장 mirror (6시간 주기, 자동):
  for repo in mirrored_repos:
    git fetch origin  (github.com에서 read)
    업데이트 반영

모니터링 CronJob (일 1회):
  for repo in mirrored_repos:
    GET /api/v1/repos/mirrors/<repo>
    if last_sync_ago > 24h: Slack alert
```

## 에러 처리 & 실패 모드

### Gitea 장애
- 이미 기동된 Pod은 pre-baked 3개 플러그인으로 작업 가능, 로컬 commit 가능, push만 실패
- auth-gateway가 신규 spawn 시 health check 실패하면 "읽기 전용 모드" 안내
- 복구 후 사용자는 축적된 commit을 수동 push

### Plugin Mirror Sync 실패
- Gitea 내장 mirror 자동 재시도 (6시간 주기, 설정값)
- 24h 연속 실패 시 Slack 알림
- 사용자는 마지막 성공 시점의 marketplace 상태 계속 사용

### NetworkPolicy 우회 시도
- **케이스 1**: remote 변경 후 github.com push → pre-push hook 1차 거부, NetworkPolicy 2차 차단
- **케이스 2**: curl/wget으로 외부 전송 → NetworkPolicy가 Gitea/Bedrock/SSO/RDS 외 전면 차단, Tier 1 감사 로그 기록

### Plugin 로드 실패
- serena 제외로 현재 failed 케이스 해결
- 향후 방어: CI에서 plugin manifest lint (구조 검증)

### Gitea 사용자 프로비저닝 실패
- auth-gateway 3회 재시도 → 실패 시 사용자 재로그인 안내 + 관리자 알림

## 검증 & 테스트

### 이미지 빌드 (CI)
- Plugin manifest lint (3개 플러그인 구조 검증)
- `plugins-config.json`에 enabled=true 확인
- `known-marketplaces.json`이 Gitea URL만 가리키는지 확인
- Dockerfile에서 github.com curl 시도 → 실패 확인

### 통합 테스트 (신규 Pod)

1. SSO 로그인 → Pod 기동 성공
2. `/plugin` 실행 → 3개 플러그인 Installed + enabled 표시
3. `superpowers:brainstorming` skill 정상 호출
4. `git clone gitea.internal.../mirrors/claude-plugins-official` → 성공
5. `git clone https://github.com/...` → 실패 (NetworkPolicy)
6. 사용자 레포 `git push gitea.internal.../users/<sso>/test.git` → 성공
7. Remote 변경 후 `git push github.com/...` → 실패 (pre-push + NetworkPolicy)

### 운영 대시보드
- Gitea mirror sync 성공률 (24h rolling)
- User Pod plugin 로드 실패율
- NetworkPolicy violation count (github.com 접근 시도)
- Gitea 가용성 (99% SLO 추적)

### 롤아웃 전략
1. Dev 환경에서 개발자 3명 × 2일 검증
2. Canary: 실무자 1~2명 × 1주 모니터링
3. 전면 배포: `kubectl rollout restart` (사용자 Pod 삭제 금지 — 재로그인 시 자동 적용)

## 제거 대상 (Cleanup)

- `container-image/config/plugins-marketplaces/` 디렉토리 전체
- `installed_plugins.json`의 serena 항목
- `known-marketplaces.json`의 GitHub 참조 7개
- `plugins-blocklist.json`의 테스트 데이터 2건

## 참조

- 기존 전체 시스템 설계: `docs/plans/2026-03-21-bedrock-claude-code-platform-design.md`
- Phase 0 review 결과: auto-memory `project_phase0_review_patterns.md`
- 실무자 운영 전환 컨텍스트: 본 브레인스토밍 세션 (2026-04-15)
