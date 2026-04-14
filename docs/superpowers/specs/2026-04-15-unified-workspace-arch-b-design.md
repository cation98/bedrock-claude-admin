# 통합 워크스페이스 설계 — Arch-B (Claude Terminal ↔ Open WebUI)

- **작성일**: 2026-04-15
- **스코프**: Phase 1a (Silent SSO) · Phase 1b (Arch-B MVP + Tier 1 로깅 + 스킬 양방향) · Phase 1c (Tier 2/3 + 감사 대시보드)
- **상태**: Design — 승인 대기
- **관련 이슈**: #30/#31 (ai-chat 이관 회귀), 신규 (통합 워크스페이스)

## 1. 목적

- 사내 Claude terminal(ttyd 기반 Pod)과 Open WebUI(ai-chat.skons.net)를 **하나의 사용자 워크스페이스**로 통합한다.
- 사용자 관점:
  1. Hub 로그인 1회로 terminal·WebUI 모두 **재로그인 없이** 이용.
  2. **파일·스킬·설정·메모리**가 양쪽에서 즉시 공유(단일 소스).
  3. 대화·프롬프트 전부가 사용자 무감각하게 **중단없이 감사·분석용으로** 로깅된다.

## 2. 비목표

- terminal과 WebUI의 대화 히스토리 동일화(의도적 분리 — L2).
- Open WebUI 자체 코드 수정(pipe·ingress·auth-gateway만 변경).
- 외부(비-SKons) 사용자 접근.

## 3. 최종 사용자 여정

1. 사용자가 `claude.skons.net` 로그인 → Hub 진입.
2. Hub에서 AI 채팅 타일 클릭 → 새 탭 `ai-chat.skons.net`.
3. 인증은 배경에서 자동 처리됨(Silent SSO). 사용자는 WebUI 화면만 본다.
4. WebUI에서 메시지 전송 → 내부적으로 사용자 본인의 Claude Pod로 라우팅 → terminal과 같은 스킬·파일·설정을 활용하여 응답.
5. 같은 세션 동안 사용자가 terminal을 열어도 **같은 `~/.claude/`와 `workspace/`** 를 공유.
6. 모든 대화·프롬프트는 시스템이 자동으로 S3로 스트리밍(사용자 조치 불필요, 비침습).

## 4. 결정 사항 (Brainstorming 요약)

| Q | 결정 | 근거 |
|---|------|------|
| 스코프 | B — 통합 아키텍처 | S4 "한 워크스페이스" 요구 |
| 시나리오 | S4 — 파일·스킬·컨텍스트 전부 공유 | 사용자 명시 |
| 아키텍처 | Arch-B — WebUI → 사용자 Pod | "terminal과 동일한 자원" |
| 세션 모델 | L2 — 파일 공유 + 대화 독립 | 혼란 방지 + 구현 현실 |
| 전송 | T1 — Pod 내부 HTTP 서버 | 표준 HTTP/SSE, 기존 ingress 재활용 |
| 로깅 범위 | C4 — 계층적 하이브리드 | 용도별 최적화 |
| 로깅 인프라 | R-우선 — 실시간 스트리밍 | "중단없음" 요구 |
| Silent SSO | SSO-1 — webui-verify 자동 refresh | 보안 원칙 유지 + UX |
| 스킬 | X1 자동 발견 MUST, X2 슬래시 forwarding SHOULD | 하드 요구사항 |

## 5. 전체 아키텍처

```
[User Browser]
   ↓   (cookie: bedrock_jwt Domain=.skons.net)
  ├── claude.skons.net ── auth-gateway (Hub, login, sessions, webui-verify)
  └── ai-chat.skons.net ── ingress-nginx
                              ↓ auth_request → auth-gateway /api/v1/auth/webui-verify
                              ↓ (access expired + refresh valid → Set-Cookie 갱신)
                           Open WebUI (shared deployment)
                              ↓ user_pod_pipe (신규)
                              ↓ HTTP POST
                [ 사용자 Pod ] http://<pod>.claude-sessions:7682/webui/chat
                 ┌───────────────────────────────────────────┐
                 │ - ttyd              (7681) terminal       │
                 │ - fileserver.py     (upload/download)     │
                 │ - webui-bridge      (7682) FastAPI+SDK    │  ← 신규
                 │ - fluent-bit        (sidecar) tail→Firehose │ ← 신규
                 │                                           │
                 │ EFS /home/node (공유)                     │
                 │  .claude/ {skills, commands, settings,   │
                 │           projects/*.jsonl, memory}       │
                 │  workspace/ { files, uploads }            │
                 └───────────────────────────────────────────┘
                              ↓ JSONL tail, PII redact
                          Kinesis Firehose
                              ↓ buffer 60s / 1MB
                  s3://bedrock-audit-{env}/
                    tier1-claude/year=…/month=…/day=…/user=…/
                    tier1-webui/…
                    tier2-shell/…   (Phase 1c)
                    tier3-recording/… (Phase 1c)
```

## 6. Phase 1a — Silent SSO

### 6.1 대상
- `auth-gateway/app/routers/auth.py::webui_verify`
- `infra/k8s/openwebui/ingress.yaml`

### 6.2 변경

**webui-verify 로직**
```
1. bedrock_jwt 검증 → 유효하면 200 + X-SKO-Email
2. 만료/없음 → bedrock_refresh 검증
3. refresh 유효 + 미revoke → 새 access token 발급
   - Set-Cookie: bedrock_jwt / bedrock_jwt_vis (Domain=.skons.net, 15분)
   - X-SKO-Email 헤더 + 200
4. refresh 무효 → 401 (auth-signin 경로 동작)
```

**ingress.yaml**
```yaml
nginx.ingress.kubernetes.io/auth-response-headers: "X-SKO-Email,X-SKO-User-Id,Set-Cookie"
```

### 6.3 검증
- ingress-nginx 현재 버전에서 auth_request Set-Cookie 전파 지원 확인(운영 클러스터 테스트 커밋 필요).
- 미지원 시 fallback: **SSO-3** (장기 `bedrock_webui_session` 쿠키 신설) — 별도 Plan B 스펙.

### 6.4 테스트
- `test_webui_verify_auto_refresh`: access 만료 + refresh 유효 → 200 + Set-Cookie
- `test_webui_verify_refresh_expired`: 둘 다 만료 → 401
- `test_webui_verify_refresh_revoked`: refresh jti blacklist → 401
- 통합 curl 시나리오 3종

### 6.5 완료 기준
- 12시간 이내 Hub 로그인 사용자가 ai-chat 클릭 시 재로그인 없이 진입.

## 7. Phase 1b — Arch-B MVP + Tier 1 로깅 + 스킬 양방향

### 7.1 신규 컴포넌트

#### 7.1.1 `webui-bridge` (Pod 내부)
- 언어: Python 3.12, FastAPI + `claude-agent-sdk`
- 포트: 7682 (cluster-internal만 노출)
- supervisord 등록(기존 ttyd, fileserver.py와 공존)

**엔드포인트**: `POST /webui/chat`
```
Headers:
  Authorization: Bearer <WEBUI_BRIDGE_SECRET>   # K8s Secret → env
  X-SKO-User-Id: <username>                      # Pipe가 OWUI trusted header forward
Body:
  {
    "session_id": "<webui-conversation-id>",
    "messages": [ {role, content}, ... ],
    "model": "us.anthropic.claude-sonnet-4-6",
    "stream": true
  }
Response: text/event-stream (SSE, Anthropic Messages API 이벤트와 호환)
```

**처리**
1. Bearer secret 검증 + `X-SKO-User-Id` vs env `OWNER_USERNAME` 일치 검증.
2. Agent SDK `query()` 또는 `ClaudeSDKClient` 호출:
   - `HOME=/home/node`, `cwd=/home/node/workspace`
   - SDK가 `~/.claude/` 자동 스캔 → 스킬/에이전트/MCP/설정 자동 적용
3. SDK stream 이벤트 → SSE chunk로 중계.
4. Claude Agent SDK가 자동 생성하는 transcript (`~/.claude/projects/-home-node-workspace/<uuid>.jsonl`) → 파일명 prefix를 `webui-<session_id>-<uuid>.jsonl`로 강제(bridge가 symlink 또는 SDK 옵션 활용) → Fluent Bit tail 대상.

#### 7.1.2 Fluent Bit sidecar
- 기본 배포 형태: **sidecar container (Pod-level)**
- 입력: `tail /home/node/.claude/projects/**/*.jsonl` + `tail /var/log/shell-events/*.jsonl` (Phase 1c)
- 필터 스테이지: PII redaction 공통 ConfigMap 적용
- 출력: Kinesis Firehose(`bedrock-audit-{env}`)
- 로컬 disk buffer: `/var/fluent-bit/state/` (EmptyDir 또는 EFS sub_path — Pod 재시작 복원용)

#### 7.1.3 Open WebUI `user_pod_pipe`
- 위치: `infra/k8s/openwebui/` 또는 Open WebUI Pipelines 패키지
- 로직:
  1. OWUI `__user__` dict에서 사번(trusted_header_auth 주입) 추출
  2. auth-gateway `/api/v1/sessions/ensure` 호출(없으면 생성) — 기존 `/sessions/` 기반
  3. Pod ready poll → `http://<pod>.claude-sessions.svc.cluster.local:7682/webui/chat` POST
  4. SSE 응답을 OWUI chat stream으로 변환(OpenAI-style `delta` chunks)
- 기존 `bedrock_ag_pipe`와 공존(기본값은 신규 pipe로 전환)

#### 7.1.4 Terraform / IaC
- `infra/terraform/audit_s3.tf`: 버킷·KMS·Lifecycle·IAM role
- `infra/terraform/firehose.tf`: Delivery Stream with Parquet(optional) + partition prefix
- `infra/k8s/platform/fluent-bit-config.yaml`: ConfigMap(filter rules), ServiceAccount, IAM binding
- `infra/k8s/platform/session-pod-template.yaml`: Pod template에 bridge·fluent-bit container 추가

### 7.2 Pod lifecycle
- WebUI 첫 메시지 + Pod 미존재 → pipe가 `/api/v1/sessions/ensure` 호출
  - 기존 `POST /sessions/` 재사용 — idempotent 수준 보장 필요
- Pod ready health check: `GET http://<pod>:7682/health` 200 + bridge·fluent-bit 준비 완료
- idle cleanup: 기존 정책 유지. WebUI 메시지도 activity로 카운트.

### 7.3 동시성 (L2)
- bridge는 요청당 **독립 Claude subprocess** spawn (SDK 기본).
- terminal Claude는 ttyd가 관리(변경 없음).
- 파일 편집 충돌: Edit 도구에 명시적 lock 없음 → "after-writer wins" 문서화.

### 7.4 스킬 양방향 (하드 요구사항)

| 스킬 유형 | Terminal | WebUI | 전략 |
|-----------|----------|-------|------|
| `.claude/skills/*.md` | ✓ | ✓ | 파일시스템 공유로 자동 |
| `.claude/commands/*.md` | ✓ | ✓ | 자연어 위임 (X1), 필요 시 슬래시 forwarding (X2) |
| `.claude/agents/*.md` | ✓ | ✓ | Task 도구 호출 |
| `.claude/plugins/**` | ✓ | ✓ | 자동 발견 |
| `~/.claude.json` MCP | ✓ | ✓ | bridge subprocess에서 stdio MCP 기동 |
| `settings.json` | ✓ | ✓ | 환경 변수·모델·훅 공유 |

**슬래시 명령 forwarding (X2)**
- WebUI 입력이 `/<name> [args]` 로 시작할 경우 pipe가 detect
- OWUI 내장 `/` 충돌 방지 접두: 일차 `//<name>` 또는 `/claude:<name>` 중 1개 채택 (첫 스파이크에서 결정)
- pipe가 system hint 추가: "사용자는 slash command `/<name>`을 호출했습니다. 해당 command를 실행하세요." → bridge → Claude가 해당 command 실행

**완료 정의 (Phase 1b Acceptance)**
1. 테스트 스킬(`/test-echo`) terminal·WebUI 동일 결과
2. 사용자 `.claude/commands/` 커스텀 명령 터미널·WebUI 양쪽 동작
3. MCP 서버(mindbase) 검색 WebUI에서도 성공
4. 사용자 메모리(`~/.claude/memory/`) terminal 추가 → WebUI 다음 메시지에서 참조 확인
5. 플러그인 기반 스킬 1건 이상 양방향 동작 확인

### 7.5 Tier 1 로깅 (실시간)

**소스**
- Tier 1a: `~/.claude/projects/**/*.jsonl` (Claude Code 자동 생성, terminal·bridge 모두)
- Tier 1b: bridge가 별도로도 기록한 `webui-<session>-*.jsonl` (중복 허용)

**파이프라인**
```
Pod fluent-bit tail → filter (PII) → Kinesis Firehose → S3
```

**S3 파티션**
```
s3://bedrock-audit-{env}/tier1-claude/year=YYYY/month=MM/day=DD/user=<sub>/{uuid}.jsonl
s3://bedrock-audit-{env}/tier1-webui/year=YYYY/month=MM/day=DD/user=<sub>/{uuid}.jsonl
```

**PII 필터 (Phase 1b 기본 규칙)**
- 이메일 `.+@.+\..+` → `***@domain`
- 주민번호 `\d{6}-[1-4]\d{6}` → `******-*******`
- 신용카드 `\d{16}` → `****************`
- AWS access key `AKIA[0-9A-Z]{16}` → `AKIA**REDACTED**`
- 전화번호(한국) `010-\d{4}-\d{4}` → `010-****-****`

**보관**
- Tier 1: 2년 (그 후 삭제)
- S3 Lifecycle: 30일 후 Glacier Deep Archive

**접근**
- KMS 고객 관리 키 암호화
- IAM: ops/audit read-only role, 삭제는 별도 approval workflow
- Athena 쿼리는 사번 파티션 필수

**"중단없음" 보장**
- Fluent Bit filesystem buffer → Firehose 일시 장애 복원
- Firehose error S3 bucket (24h 내 재전송)
- **사용자 Claude 경로엔 로깅 dependency 0** — 파이프라인 전체 정지해도 사용자 영향 없음

**기존 RDS `prompt_audit_*` 테이블 처리**
- Phase 1b 중: 유지(변경 없음) + 배치 폴링 계속
- Phase 1c에서 배치 폴링 제거, S3 기반 재구축

### 7.6 사용자 통지
- Hub UI 하단 고지문: "본 플랫폼의 모든 대화는 감사·분석·보안 목적으로 기록됩니다."
- 로그인 최초 1회 ToS 동의(체크박스) — 별도 디자인 세션, Phase 1b 배포 전 완료 필수

### 7.7 보안
- bridge ↔ pipe 통신: Pod 기동 시 생성되는 shared secret(K8s Secret), Pod 수명 동안만 유효
- bridge 포트 7682: cluster-internal만(Network Policy), external ingress 미노출
- Fluent Bit IAM: Firehose PutRecord 최소 권한 IRSA
- S3 버킷: public access block, delete protection

## 8. Phase 1c — Tier 2/3 + 감사 대시보드

- Tier 2 shell 이벤트: bash `PROMPT_COMMAND` 또는 `/etc/profile.d/audit.sh`에서 명령명·인자 일부 기록 → `/var/log/shell-events/*.jsonl` → Fluent Bit
- Tier 3 세션 녹화: ttyd 녹화(`ttyd -r` + asciinema) 샘플링 정책(예: 10% 또는 특정 사용자)
- Athena 테이블(partitioned by year/month/day/user) + AWS Glue Data Catalog
- Admin Dashboard 감사 화면(기존 analytics/ui-split 패턴 확장)
- 배치 폴링(`prompt_audit_service.collect_and_analyze`) 제거 → S3 기반 집계로 전환
- S3 Lifecycle 본격 적용(Glacier, 삭제)

## 9. 리스크 레지스터

| # | 리스크 | 영향 | 완화 | 소유 |
|---|-------|------|------|------|
| 1 | ingress-nginx auth_request Set-Cookie 미전파 | Silent SSO 실패 | SSO-3 Plan B로 1일 전환 | Phase 1a |
| 2 | Pod 미존재 시 자동 생성 지연 (10-30s) | WebUI 첫 메시지 UX 저하 | 로딩 이벤트 + progressive SSE | Phase 1b |
| 3 | 동일 파일 동시 편집 충돌 | 데이터 손실 | 문서화, Phase 2 edit lock 검토 | Phase 2 |
| 4 | Claude Agent SDK breaking change | bridge 장애 | SDK 버전 pin + 업그레이드 테스트 | Phase 1b |
| 5 | 로그량 폭증 (S3 비용) | 월 비용 증가 | buffer 60s/1MB, Parquet, Glacier lifecycle | Phase 1b |
| 6 | PII redaction 누락 | 규정 위반 | 정규식 + Athena 주기 검사 + 감사팀 접근권 최소화 | Phase 1b |
| 7 | Pod 재시작 시 Fluent Bit 버퍼 유실 | 일부 로그 누락 | filesystem buffer persistent | Phase 1b |
| 8 | Bridge secret 유출 | 무단 Claude 실행 | Network Policy cluster-internal only + TLS | Phase 1b |
| 9 | 사용자 동의 미완 | 법적 리스크 | Phase 1b 배포 전 ToS 업데이트 + 명시적 동의 | Phase 1b |
| 10 | Slash command 접두 충돌 | UX 혼란 | 첫 스파이크에서 규약 결정(`//` vs `/claude:`) | Phase 1b |

## 10. 완료 기준 (Phase 1b 전체)

1. Silent SSO: 12h 이내 Hub 로그인 사용자가 ai-chat 재로그인 없이 진입 (Phase 1a에서 이미 1차 완료)
2. Arch-B: WebUI 메시지가 사용자 Pod의 Claude Code로 라우팅, 응답 수신
3. 스킬 양방향: §7.4 Acceptance 5종 PASS
4. Tier 1 로깅: Claude 대화 JSONL이 S3 파티션에 60초 이내 착륙, PII 필터 샘플 검증 PASS
5. 사용자 통지 고지문 + ToS 동의 UX 배포
6. E2E 5명 dogfood 통과(버그 critical 0)
7. 리스크 #1/#6/#9 해소 확인

## 11. 미해결(TBD)

- Fluent Bit 배포 형태(sidecar vs DaemonSet) 최종 — 첫 스파이크에서 결정
- 슬래시 명령 접두 규약(`//` vs `/claude:`) — 첫 스파이크에서 결정
- ToS 동의 UX 구체 설계 — 별도 디자인 세션
- Athena 스키마·쿼리 패턴 — Phase 1c

## 12. 관련 문서

- `docs/plans/2026-03-21-bedrock-claude-code-platform-design.md` (전체 시스템)
- `docs/decisions/phase1a-samesite-strict-vs-lax.md` (쿠키 정책)
- `docs/superpowers/specs/2026-04-12-phase1a-security-hardening-design.md` (access TTL 15분 결정)
- `ops/export/README.md` (현재 감사 export 상태)
