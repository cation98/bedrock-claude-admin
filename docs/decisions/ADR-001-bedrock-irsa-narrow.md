# ADR-001: 사용자 Pod IRSA에서 Bedrock 권한 제거

- **Status**: **Superseded (2026-04-14)** — 원상복구됨. 아래 "Postmortem" 섹션 참조.
- **Date**: 2026-04-14
- **Issue**: [#20 Console Pod AWS SDK → HTTP proxy 마이그레이션 (T20)](https://github.com/cation98/bedrock-ai-agent/issues/20)
- **Spec**: `docs/superpowers/specs/2026-04-14-bedrock-irsa-narrow-design.md`
- **Plan**: `docs/superpowers/plans/2026-04-14-bedrock-irsa-narrow.md`
- **Verification**: `docs/poc/2026-04-14-issue-20-irsa-narrow-verification.md`

## Context

T20의 목적은 사용자 Pod의 Bedrock 호출 경로를 **Bedrock AG proxy로 단일화**하여 사용량 추적·audit·rate-limit 중앙화를 달성하는 것이다. 완료된 부분:

- Bedrock AG Anthropic-compat 엔드포인트 (`bedrock_proxy.py` MODEL_MAP)
- `entrypoint.sh` — `ANTHROPIC_BASE_URL` 주입 + JWT 교환 + `unset CLAUDE_CODE_USE_BEDROCK`

하지만 사용자 Pod은 IRSA role `bedrock-claude-bedrock-access`를 통해 여전히 `bedrock:InvokeModel*` 권한을 보유 → 사용자가 `aws bedrock-runtime invoke-model` 명령으로 proxy를 우회한 직접 호출이 가능했다.

네트워크 레벨 해결(FQDN egress 차단)은 클러스터 CNI가 AWS VPC CNI(표준 NetworkPolicy)이므로 FQDN 기반 차단 불가하며, squid egress gateway는 미배포 상태다.

## Decision

IAM 레벨에서 `bedrock-claude-bedrock-invoke` 인라인 policy에서 `AllowBedrockInvoke`, `AllowModelDiscovery` 2개 statement를 제거한다. 사용자가 직접 AWS SDK를 사용해도 `AccessDenied`를 받도록 한다.

**유지**: S3(tango-alarm-logs), Athena, Glue 권한 4개 statement — Tango 알람 분석 use case 필수.

## Alternatives Considered

- **A. Whitelist-only NetworkPolicy egress** — 표준 NetworkPolicy는 FQDN 불가, IP CIDR 기반은 관리 비용 高, 기각.
- **B. Squid egress gateway 도입** — FQDN 차단 가능하나 신규 인프라 배포 + Claude Code CLI HTTP_PROXY 인식 검증 필요. Phase 2로 연기.
- **C.1 (채택) IAM narrow** — 최소 수정(2개 statement 제거), 직접 호출 차단이라는 핵심 목적 달성, Tango 무영향.
- **C.2 IAM + NetworkPolicy 타이트닝** — C.1에 더해 `443 anywhere`를 좁힘. Tango S3/Athena가 public endpoint 경유이므로 실익 낮고 실행 리스크 有. 기각.
- **C.3 ServiceAccount 분리** — Tango용 별도 SA 부여 + Pod 안에서 assume-role. 복잡도 高, Phase 1 일정 외. 기각.

## Consequences

### 즉시 효과 (+)

- 사용자의 `aws bedrock-runtime invoke-model` 직접 호출 → `AccessDenied` (IAM 레벨 강제)
- Bedrock AG proxy가 유일한 Bedrock 접근 경로 → 사용량 집계 누락 방지
- Claude Code CLI는 entrypoint.sh 로직에 의해 proxy 사용 → 사용자 변경 영향 0

### 즉시 효과 (-)

- Tango use case 사용자가 **의도치 않게** `aws bedrock-runtime`로 LLM을 붙이려던 실험 코드가 있다면 실패 (CloudTrail 0건 사전 확인으로 실제 영향 없음 검증됨)
- 신규 AI 서비스 추가 시 권한 재검토 필요 (좋은 제약)

### 리스크 및 완화

- **IAM eventual consistency** (최대 1분) — apply 직후 Pod 호출 테스트 시 과도기 가능. Task 6 검증은 apply 후 충분한 시간 경과 상태에서 수행하여 AccessDenied 확인됨.
- **Terraform state drift 재발**: apply에 `-target=aws_iam_role_policy.bedrock_invoke`를 사용하여 사전 drift 9건을 건드리지 않음. 사후 `terraform plan` 재실행 결과 대상 리소스 drift 없음 확인.

### Deferred Risks (이 결정으로 해소되지 않음 → Phase 2 squid 필요)

1. **FQDN-level egress 차단 부재** — 사용자가 자체 `ANTHROPIC_API_KEY` 설정 시 `api.anthropic.com` 직접 호출 차단 불가
2. **기타 AI API 엔드포인트** (OpenAI, Gemini 등) 직접 호출 차단 불가
3. **Tango 경로의 S3/Athena는 NAT 경유 public endpoint** — VPC endpoint 전환은 별도 과제
4. **9개 사전 Terraform state drift** — 이번 PR에서 건드리지 않음, 별도 이슈 필요

## 실행 기록

- iam.tf 수정 커밋: `9e8b25c` (branch `phase1-backlog/#20-irsa-narrow`)
- Apply 방식: `terraform apply -target=aws_iam_role_policy.bedrock_invoke`
- Apply 결과: `Resources: 0 added, 1 changed, 0 destroyed.`
- 사전 검증 (CloudTrail 7일): 직접 호출 0건 (`/tmp/issue20-cloudtrail-bedrock-invoke.txt`)
- 사후 검증 (테스트 Pod): Bedrock AccessDenied ✓, Tango S3 prefix list ✓, proxy HTTP 401 ✓

## 롤백 경로

1. **Terraform 롤백**: `git revert 9e8b25c && terraform apply -target=aws_iam_role_policy.bedrock_invoke`
2. **긴급 우회**: AWS Console에서 policy JSON 직접 수정(Terraform drift 발생하나 긴급 시 허용)

## Postmortem (2026-04-14)

본 결정은 **프로덕션 회귀를 유발하여 원상복구**되었다. 교훈:

### 증상
적용 직후 사용자 터미널에서 `403 not authorized to perform: bedrock:InvokeModelWithResponseStream` 에러 발생. 사용자가 재시도 루프에 갇힘.

### 근본 원인 (스펙 가정의 오류)

| 가정 | 실제 |
|------|------|
| "entrypoint.sh가 `unset CLAUDE_CODE_USE_BEDROCK`하므로 proxy 경로만 사용" | **ttyd spawn 경로에 unset 반영 안 됨**. `/proc/1/environ`에 `CLAUDE_CODE_USE_BEDROCK=1` 그대로 유지 |
| "ANTHROPIC_BASE_URL이 유일한 호출 경로" | **`CLAUDE_CODE_USE_BEDROCK=1`이 우선**하여 Claude Code는 AWS SDK 경로 택 |
| "CloudTrail 7일 0건 = 안전" | 조회 시점에 **사용자 Pod 0개**였음 (실사용자 부재 타이밍 오판) |
| "Tango S3/Athena만 활성 권한" | **Bedrock이 핵심 활성 권한**이었음 |

### 실제 활성 Bedrock 호출 경로

```
사용자 터미널(ttyd bash) → Claude Code CLI
  → CLAUDE_CODE_USE_BEDROCK=1 감지
  → aws-sdk-js 로드 → IRSA (AWS_ROLE_ARN) assume-role
  → bedrock-runtime.ap-northeast-2 직접 호출
```

proxy 경로(`bedrock_proxy.py`)는 **설계되었으나 실사용 안 되는 상태**였다.

### 복구 경로

1. `aws iam put-role-policy` 로 AllowBedrockInvoke/AllowModelDiscovery 수동 복구 (즉시)
2. `infra/terraform/iam.tf` 도 원상복구 PR로 state drift 해소 (후속)

### Phase 2로 이관

진정한 "proxy-only 전환"은 다음 조건 모두 충족 후 재시도:

1. Pod-level env에서 `CLAUDE_CODE_USE_BEDROCK=1` 제거 (Claude Code CLI가 `ANTHROPIC_BASE_URL` 경로 실제 사용하도록)
2. Squid egress gateway 도입으로 IP/FQDN-level 차단 보장 (IAM 단독 제어는 aws-sdk-js가 여전히 창구)
3. 충분한 활성 사용자 환경에서 E2E 회귀 테스트 (단순 CloudTrail 조회 불충분)

## 재검토 트리거

다음 중 하나 발생 시 이 결정을 재검토:
- Phase 2 squid egress gateway 배포 시 (IAM 제약 유지 vs 네트워크 레벨 이관 판단)
- Tango 이외의 use case가 Bedrock 직접 호출을 요구할 때 (새로운 SA 분리 검토)
- AWS Bedrock이 계정 단위 SCP로 제어 가능해지면 (IAM role-level 대신 상위 경계 이동 검토)
