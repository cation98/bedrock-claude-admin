# Bedrock IRSA 권한 축소 설계 (Issue #20)

**작성일**: 2026-04-14
**이슈**: [#20 refactor: Console Pod AWS SDK → HTTP proxy 마이그레이션 (T20)](https://github.com/cation98/bedrock-ai-agent/issues/20)
**브랜치**: `phase1-backlog/#20-irsa-narrow`

## 1. 배경

이슈 #20(T20)의 목적은 **사용자 Pod에서 Bedrock 접근 경로를 Bedrock AG proxy로 단일화**하여 다음을 달성하는 것이다:

- 사용량 추적의 일관성 (auth-gateway JWT 기반 attribution)
- 모델 호출 중앙화 (rate limit, audit, cost control)
- 보안 경계 명확화

완료된 부분:
- ✅ `bedrock_proxy.py` MODEL_MAP 구현 (Anthropic 호환 엔드포인트)
- ✅ `entrypoint.sh` — `ANTHROPIC_BASE_URL` 주입 + JWT 교환 + `unset CLAUDE_CODE_USE_BEDROCK`

**남은 갭**: Claude Code CLI는 proxy를 사용하나, 사용자 Pod의 IRSA role(`bedrock-claude-bedrock-access`)에는 여전히 `bedrock:InvokeModel*` 권한이 있어 사용자가 `aws bedrock-runtime` 명령으로 **proxy를 우회한 직접 호출이 가능**하다.

## 2. 목표 및 비목표

### 목표

1. 사용자 Pod IRSA에서 Bedrock 관련 IAM action 제거 → 직접 호출 시 `AccessDenied` 반환
2. Tango 알람 분석 use case(S3 + Athena + Glue)는 **영향 없이 보존**
3. 의사결정 근거를 ADR로 문서화하여 향후 회귀 방지

### 비목표 (이번 범위 제외)

- NetworkPolicy egress FQDN-level 차단 → Phase 2에 squid 도입으로 해결
- ServiceAccount 분리(Tango용 별도 SA) → 복잡도 高, Phase 1 일정 외
- `k8s_service.py`의 `ANTHROPIC_BASE_URL` trailing `/v1` 이슈 → 별도 이슈 #26
- 백엔드 서비스(auth-gateway, usage-worker 등) IRSA는 이번 변경과 무관

## 3. 현재 상태 (검증 기반)

### IAM role: `bedrock-claude-bedrock-access`

Customer managed policy `bedrock-claude-bedrock-invoke` 내 Statement 6개:

| Sid | 리소스 | 판정 |
|-----|--------|------|
| `AllowBedrockInvoke` | `bedrock:InvokeModel*` on Anthropic foundation models | ❌ **제거 대상** |
| `AllowModelDiscovery` | `bedrock:ListFoundationModels`, `bedrock:GetFoundationModel` | ❌ **제거 대상** |
| `AllowTangoS3Read` | S3 `tango-alarm-logs` GetObject/ListBucket/GetBucketLocation | ✅ 유지 |
| `AllowTangoAthenaQuery` | Athena workgroup `primary` | ✅ 유지 |
| `AllowTangoGlueCatalog` | Glue catalog + database `tango_logs` | ✅ 유지 |
| `AllowAthenaResultsWrite` | S3 `tango-alarm-logs/athena-results/*` Put/Get | ✅ 유지 |

### 사용자 Pod 경로

- 사용자 Pod SA: `claude-terminal-sa` (IRSA 연결됨)
- entrypoint.sh: `ANTHROPIC_BASE_URL` + JWT 교환 → Claude Code CLI는 proxy 사용
- `CLAUDE_CODE_USE_BEDROCK` unset → CLI의 AWS SDK fallback 비활성화

### 네트워크 상황

- CNI: AWS VPC CNI (FQDN NetworkPolicy 미지원)
- Bedrock VPC Endpoint: `vpce-0fab76c138da3a84a` (내부 경로)
- Squid egress gateway: **미배포**
- `allow-user-pod-traffic` egress: `443 anywhere` 포괄 허용 (현 시점 유지)

## 4. 설계

### 4.1 IAM 변경

**파일**: `infra/terraform/iam.tf`
**대상 리소스**: `aws_iam_role_policy.bedrock_invoke` (정책 이름 `bedrock-claude-bedrock-invoke`)

**변경 내용**: 인라인 policy의 `AllowBedrockInvoke` + `AllowModelDiscovery` 2개 statement 제거. S3/Athena/Glue 4개 statement는 그대로 유지.

### 4.2 ADR 문서

**파일**: `docs/decisions/ADR-001-bedrock-irsa-narrow.md` (프로젝트 첫 ADR이므로 번호 001)

**섹션**:
1. Context — #20 이슈 배경, T20 완료 상태, IRSA 갭
2. Decision — IRSA에서 Bedrock action 제거, Tango 권한 보존
3. Alternatives Considered — A(whitelist egress) / B(squid) / C.1(IAM narrow, 채택) / C.2(IAM+net narrowing) / C.3(SA split)
4. Consequences — 즉시 효과 / 리스크 / Phase 2 squid로 이관된 과제
5. Deferred Risks — FQDN-level 차단 부재, Tango 경로 비암호화 외부 통신 등

### 4.3 검증 리포트

**파일**: `docs/poc/2026-04-14-issue-20-irsa-narrow-verification.md`

**내용**:
- 사전: CloudTrail 조회 결과(최근 7일 `claude-terminal-sa`의 `bedrock:Invoke*` 호출 건수)
- 사후: `aws iam get-role-policy` diff, 테스트 Pod CLI 경로 성공/직접 호출 거부 증거

## 5. 실행 순서

1. **[worktree 생성됨]** `phase1-backlog/#20-irsa-narrow`
2. **사전 검증** — CloudTrail 조회. 직접 호출 **0건**이어야 안전. 1건 이상이면 중단하여 범위 재검토.
3. **iam.tf 수정** — 2개 statement 제거
4. **`terraform plan`** — diff 수동 검토
5. **`terraform apply`** — 정책 교체 (AWS 실 반영)
6. **사후 검증** — 테스트 Pod 스폰 + CLI prompt 성공 + 수동 `aws bedrock-runtime` 실패 확인
7. **ADR + 검증 리포트 작성**
8. **커밋 + PR → main 머지**
9. **이슈 #20 close** (검증 결과 코멘트)

## 6. 롤백 계획

- **즉시 롤백 (Terraform)**: 이전 commit으로 체크아웃 → `terraform apply` → 정책 원복
- **긴급 우회 (수동 IAM)**: AWS Console에서 정책 JSON 직접 편집으로 `AllowBedrockInvoke` 복구 (Terraform drift 발생하나 긴급 시 허용)
- **검증 실패 시**: 변경 전 iam.tf 상태로 즉시 revert PR 발행, main 머지 후 apply

## 7. 리스크 & 완화

| 리스크 | 가능성 | 영향 | 완화 |
|--------|-------|------|------|
| 사용자가 현재 직접 Bedrock 호출 중 | 낮 | 서비스 영향 | 사전 CloudTrail 0건 확인 후 진행 |
| Tango use case 오작동 | 매우 낮 | Tango 데모 영향 | S3/Athena/Glue statement 건드리지 않음 |
| Claude Code CLI proxy 경로 회귀 | 낮 | CLI 기능 영향 | entrypoint.sh 미변경, 사후 smoke 테스트 |
| 다른 SA에 IRSA role 공유 | 중 | 타 서비스 권한 축소 | `aws iam list-instance-profiles-for-role` + k8s SA `role-arn` grep 으로 단일 점유 확인 |
| Terraform state drift | 낮 | 후속 apply 충돌 | apply 후 `terraform plan` = no changes 확인 |

## 8. Deferred Risks (ADR에 명시)

아래 항목은 본 변경으로 **해소되지 않는** 보안 갭이다:

1. **FQDN-level egress 차단 부재** — 사용자 Pod에서 외부 `api.anthropic.com`(만약 본인 API key 설정 시), 기타 AI 엔드포인트 직접 호출 차단 불가
2. **squid/egress gateway 미도입** — Phase 2에서 해결 예정
3. **Tango 경로의 S3/Athena 호출은 현재 NAT 경유 public endpoint** — VPC endpoint 마이그레이션 별도 과제

## 9. 성공 판정 기준

모두 True여야 이슈 #20 close 가능:

- [ ] Terraform apply 성공, state drift 없음
- [ ] `aws iam get-role-policy` 결과에 `AllowBedrockInvoke`/`AllowModelDiscovery` 부재
- [ ] 테스트 Pod에서 Claude Code CLI 호출 → Bedrock AG proxy 경로로 성공
- [ ] 테스트 Pod에서 `aws bedrock-runtime invoke-model ...` → `AccessDenied` 반환
- [ ] Tango 경로 체크: `aws s3 ls s3://tango-alarm-logs/` 성공 (권한 보존 확인)
- [ ] ADR 및 검증 리포트 커밋됨
