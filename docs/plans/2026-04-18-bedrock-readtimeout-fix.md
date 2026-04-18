# Bedrock ReadTimeout & Liveness Fix Plan

**작성**: 2026-04-18
**상태**: PLAN (미실행)
**관련**: mindbase `issue-auth-gateway-bedrock-readtimeout-liveness-20260416`

## 문제 요약

`/v1/messages` 엔드포인트가 Sonnet 4.6 global cross-region 프로필 호출 시 50초+ 응답 지연 후 500 발생.
부수적으로 워커가 timeout 처리 중 `/health` 응답 못 줘 Liveness 3연속 fail → kubelet kill → Pod 재시작.

### 관측 데이터 (2026-04-16 12:51~15:51 KST)
- `auth-gateway-75c4f57867-2cl6z`, `-xffd4` 각각 RESTART=4
- Liveness fail: `x41 over 3h9m`, `x38 over 140m`
- 종료: SIGTERM Exit 0 (kubelet liveness fail kill)
- 에러: `botocore.exceptions.ReadTimeoutError: Read timeout on endpoint URL: bedrock-runtime.ap-northeast-2.amazonaws.com/model/global.anthropic.claude-sonnet-4-6/invoke (read timeout=60)`

### 사용자 체감 증상
- Claude Code CLI 응답 50초+ 지연
- `aws bedrock` 직접 호출은 정상 → Bedrock 자체 문제 아닌 호출 패턴 문제

## 가설 (검증 필요)

1. **Sonnet 4.6 global 프로필 first-token 지연** — cross-region inference profile이 초기 라우팅에 시간 소요
2. **boto3 동기 호출이 이벤트 루프 블로킹** — `_invoke_bedrock`/`_stream_bedrock`이 `run_in_executor`로 격리되긴 하지만 default executor 워커 풀이 작거나 큐잉
3. **read_timeout 60초가 단일 폴백 없이 절대값** — Bedrock streaming은 first-token까지 수십 초 가능
4. **Liveness probe `timeout=1s`가 너무 짧음** — 워커 1개라도 Bedrock 호출 중이면 `/health` 응답 지연

## 진단 우선 (실행 전 필수)

### 재현 절차
1. 사용자 Pod 1개 spawn (Hub 로그인)
2. Pod 내 `claude --debug` 모드로 호출
3. auth-gateway 로그에 `_invoke_bedrock` 시작/종료 timestamp 추가
4. 동일 시간에 `/health` 응답 시간 캡처
5. AWS CloudWatch `AWS/Bedrock Invocations` p50/p99 지연 비교

### 측정 항목
- first-token latency (streaming `message_start` 도착까지)
- 전체 응답 latency
- `_check_user_quota` DB 쿼리 시간 (별도 의심 후보)
- executor pool 워커 사용률 (FastAPI default `min(32, cpu+4)`)

## 조치 후보

### Tier 1 — 안전한 즉시 조치
- [ ] **Liveness probe timeout 1s → 5s** (`infra/k8s/platform/auth-gateway.yaml`) — Pod 재시작 루프만 차단, latency는 그대로
- [ ] **boto3 Config(read_timeout=120, connect_timeout=10)** — 60초 timeout이 일찍 죽이는 것이라면 완화

### Tier 2 — 구조 개선
- [ ] **dedicated thread pool** for Bedrock invoke (`ThreadPoolExecutor(max_workers=N)`) — default executor 격리하여 다른 핸들러 블로킹 방지
- [ ] **boto3 → aioboto3 전환** — 진정한 async I/O로 이벤트 루프 활용
- [ ] **streaming early-yield** — `message_start` 도착 즉시 클라이언트에 첫 SSE 보내 perceived latency 단축

### Tier 3 — AWS 측
- [ ] **Sonnet 4.6 global profile p99 latency 문의** — re-routing overhead 정상치 확인
- [ ] **us. 또는 ap. profile fallback** — global 지연 시 region-specific으로 전환

## 영향 범위

- **수정 파일** (예상):
  - `auth-gateway/app/routers/bedrock_proxy.py` (executor pool, read_timeout)
  - `infra/k8s/platform/auth-gateway.yaml` (liveness)
  - `auth-gateway/requirements.txt` (aioboto3 도입 시)
- **사용자 영향**: 전 Claude 사용자 응답 지연 해소
- **롤백 가능성**: liveness/timeout 조정은 즉시 revert 가능, executor 변경은 신중

## 비실행 결정 사유 (2026-04-18)

체크포인트 메모의 "x-api-key 미지원" 가설이 잘못이었음을 확인 (mindbase 분석 자료가 명확한 ReadTimeout root cause를 가리킴). 본 plan은 다음 세션에서:
1. 진단 절차 먼저 실행 → 가설 1~4 중 어느 것이 주범인지 확정
2. Tier 1부터 순차 적용 + 효과 측정
3. Tier 2/3는 Tier 1 결과에 따라 결정

## 관련 기록

- mindbase: `issue-auth-gateway-bedrock-readtimeout-liveness-20260416`
- mindbase: `guide-claude-code-hang-prevention` (유형 A/B 분류, 본 이슈는 별건)
- 이전 체크포인트: `20260418-2210-plugin-slim-marketplace-resolved.md`, `20260418-213957-plugin-deploy-hotfix-complete.md`
- 잘못된 가설 출처: 위 두 체크포인트 Remaining Work #2/#3 ("x-api-key 미지원이 50s 지연 원인")
