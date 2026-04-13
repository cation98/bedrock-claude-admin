# Bedrock IRSA 권한 축소 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자 Pod IRSA role `bedrock-claude-bedrock-access`에서 Bedrock 관련 IAM statement 2개를 제거하여 Bedrock AG proxy를 단일 호출 경로로 IAM 레벨에서 강제한다.

**Architecture:** Terraform으로 관리되는 IAM inline policy `bedrock-claude-bedrock-invoke`의 Statement 배열에서 `AllowBedrockInvoke`, `AllowModelDiscovery` 2개만 제거. Tango 관련 S3/Athena/Glue 4개 statement는 그대로 유지. 변경은 `terraform plan → apply` 경로로 적용하고, 사전/사후 검증을 리포트로 남긴다.

**Tech Stack:** Terraform (AWS provider), AWS IAM, AWS CloudTrail, kubectl, AWS CLI, Claude Code CLI

**Spec:** `docs/superpowers/specs/2026-04-14-bedrock-irsa-narrow-design.md`

---

## File Structure

### Modify
- `infra/terraform/iam.tf:46-142` — resource `aws_iam_role_policy.bedrock_invoke` 의 policy JSON 배열에서 Statement 2개 제거

### Create
- `docs/decisions/ADR-001-bedrock-irsa-narrow.md` — 의사결정 기록 (ADR 최초이므로 번호 001)
- `docs/poc/2026-04-14-issue-20-irsa-narrow-verification.md` — 사전/사후 검증 결과 리포트

### Untouched (중요 — 건드리지 말 것)
- `infra/terraform/iam.tf:187-220` (`aws_iam_role_policy.bedrock_ag_invoke`) — Bedrock AG 자체 권한, 유지
- `infra/terraform/iam.tf:274+` (`aws_iam_role_policy.auth_gateway_bedrock_invoke`) — auth-gateway IRSA, 유지
- `infra/k8s/pod-template.yaml` — Pod 스펙 변경 없음
- `container-image/entrypoint.sh` — 이미 proxy 경로 구성됨, 변경 없음

---

## Task 1: 사전 검증 — CloudTrail 직접 호출 조회

**목적**: `claude-terminal-sa`가 발급한 세션이 최근 7일간 `bedrock:InvokeModel*`을 직접 호출한 건수를 확인. **0건이어야 안전하게 진행 가능**하다.

**Files:**
- Create (작업 중 임시, 마지막에 리포트로 옮김): 사전 검증 결과 텍스트

- [ ] **Step 1: CloudTrail 쿼리 실행**

`claude-terminal-sa`로 assume-role 되는 세션 이름 패턴은 `botocore-session-*` 또는 IRSA 표준 `bedrock-claude-bedrock-access`. 다음 명령으로 조회:

```bash
aws cloudtrail lookup-events \
  --region ap-northeast-2 \
  --lookup-attributes AttributeKey=EventName,AttributeValue=InvokeModel \
  --start-time "$(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --max-results 50 \
  --query 'Events[?contains(Resources[].ResourceName, `bedrock-claude-bedrock-access`) || contains(CloudTrailEvent, `bedrock-claude-bedrock-access`)].{Time:EventTime,User:Username,Source:SourceIPAddress}' \
  --output table 2>&1 | tee /tmp/issue20-cloudtrail-bedrock-invoke.txt
```

**Note**: `date` 명령 문법이 macOS(BSD)와 Linux(GNU)에서 다르므로 위 이중 식으로 호환.
**Note**: CloudTrail은 management events 기본 수집이며 Bedrock InvokeModel 호출 기록에는 지연(최대 15분) 있음.

추가로 `InvokeModelWithResponseStream`도 조회:

```bash
aws cloudtrail lookup-events \
  --region ap-northeast-2 \
  --lookup-attributes AttributeKey=EventName,AttributeValue=InvokeModelWithResponseStream \
  --start-time "$(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --max-results 50 \
  --query 'Events[?contains(CloudTrailEvent, `bedrock-claude-bedrock-access`)].{Time:EventTime,User:Username,Source:SourceIPAddress}' \
  --output table 2>&1 | tee -a /tmp/issue20-cloudtrail-bedrock-invoke.txt
```

- [ ] **Step 2: 결과 판정**

```bash
cat /tmp/issue20-cloudtrail-bedrock-invoke.txt
```

**판정 기준**:
- 결과 테이블이 비어 있거나 "No events found": ✅ 진행
- 1건 이상: ⚠ **중단**. User Pod에서 직접 호출 중 → 차단 시 서비스 영향. 이슈 원문 재검토 필요.

**현재 상태 힌트**: `kubectl get pods -n claude-sessions -l app=claude-terminal`가 0개 → 실행 중인 사용자 Pod이 없어 직접 호출 가능성이 매우 낮음.

- [ ] **Step 3: 결과 기록**

결과(0건이든 N건이든)를 검증 리포트 초안에 기록. 이 단계에서는 `/tmp/issue20-cloudtrail-bedrock-invoke.txt` 보존만 하면 됨. Task 7에서 최종 리포트에 포함.

---

## Task 2: iam.tf 수정 — Statement 2개 제거

**목적**: `aws_iam_role_policy.bedrock_invoke` 의 policy JSON 배열에서 `AllowBedrockInvoke`(lines 53-70), `AllowModelDiscovery`(lines 71-79) 2개 statement 제거.

**Files:**
- Modify: `infra/terraform/iam.tf:46-142`

- [ ] **Step 1: 현재 상태 저장 (롤백 대비)**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow
cp infra/terraform/iam.tf infra/terraform/iam.tf.bak-issue20
```

- [ ] **Step 2: Edit 도구로 2개 statement 제거**

Edit 도구 사용. old_string은 `Statement = [` 다음 줄부터 `AllowModelDiscovery` statement 끝의 `},` 까지(Tango statement 직전 빈 줄 포함). new_string은 `Statement = [` 다음에 곧장 Tango 주석이 오도록.

**old_string** (정확히 일치해야 함 — iam.tf:52-80):

```
    Statement = [
      {
        Sid    = "AllowBedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          # Foundation models (직접 호출)
          "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
          # Cross-region inference profiles (account ID 포함 필수)
          "arn:aws:bedrock:*:680877507363:inference-profile/us.anthropic.claude-*",
          "arn:aws:bedrock:*:680877507363:inference-profile/global.anthropic.claude-*",
          # Global inference profiles
          "arn:aws:bedrock:*::inference-profile/us.anthropic.claude-*",
          "arn:aws:bedrock:*::inference-profile/global.anthropic.claude-*"
        ]
      },
      {
        Sid    = "AllowModelDiscovery"
        Effect = "Allow"
        Action = [
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModel"
        ]
        Resource = "*"
      },
      # ----- TANGO 알람 데이터 접근 -----
```

**new_string**:

```
    Statement = [
      # ----- TANGO 알람 데이터 접근 -----
      # NOTE: Bedrock 관련 statement 2개(AllowBedrockInvoke, AllowModelDiscovery)는
      # 2026-04-14 issue #20로 제거됨. 사용자 Pod의 Bedrock 경로는 Bedrock AG proxy
      # (openwebui/bedrock-ag-sa IRSA)를 통해서만 허용된다.
      # ADR: docs/decisions/ADR-001-bedrock-irsa-narrow.md
```

- [ ] **Step 3: 수정 결과 확인**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow
grep -n "AllowBedrockInvoke\|AllowModelDiscovery\|AllowTango" infra/terraform/iam.tf | head -20
```

**Expected output** (첫 policy의 Bedrock action 2개가 사라지고 Tango 4개만 남아야 함):

```
195:        Sid    = "AllowBedrockInvoke"       # bedrock_ag_invoke (keep)
210:        Sid    = "AllowModelDiscovery"      # bedrock_ag_invoke (keep)
... (Tango Sids)
```

첫 policy 블록(L46-142 범위 내)에는 `AllowBedrockInvoke`/`AllowModelDiscovery`가 없어야 한다.

- [ ] **Step 4: Terraform 포맷 검증**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow/infra/terraform
terraform fmt -check iam.tf
```

**Expected**: 출력 없음(no diff). diff 있으면 `terraform fmt iam.tf`로 정렬.

- [ ] **Step 5: 커밋 (apply 전 단계 커밋 — 롤백 기점)**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow
rm infra/terraform/iam.tf.bak-issue20
git add infra/terraform/iam.tf
git commit -m "$(cat <<'COMMIT'
refactor(phase1-backlog/#20): IRSA claude-terminal Bedrock statement 제거

bedrock-claude-bedrock-invoke 인라인 policy에서 AllowBedrockInvoke,
AllowModelDiscovery 2개 statement 제거. 사용자 Pod은 Bedrock AG proxy
경로만 사용하도록 IAM 레벨 강제.

Tango use case용 S3/Athena/Glue 4개 statement는 유지.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
COMMIT
)"
```

---

## Task 3: Terraform plan 검토

**목적**: 본 변경이 **오직 해당 policy만** 수정하고 다른 리소스에 drift를 일으키지 않음을 확인.

- [ ] **Step 1: plan 실행 및 캡처**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow/infra/terraform
terraform plan -out=/tmp/issue20-tfplan 2>&1 | tee /tmp/issue20-tfplan.log
```

- [ ] **Step 2: 변경 리소스 확인**

```bash
grep -E "^\s*#\s*(aws|module)\." /tmp/issue20-tfplan.log
```

**Expected** (정확히 1개 리소스만 수정):

```
  # aws_iam_role_policy.bedrock_invoke will be updated in-place
```

다른 리소스가 수정/생성/삭제되는 경우 **중단**하고 원인 파악. 예상치 못한 drift면 apply 금지.

- [ ] **Step 3: diff 내용 확인**

```bash
grep -A 3 "AllowBedrockInvoke\|AllowModelDiscovery" /tmp/issue20-tfplan.log | head -20
```

**Expected**: 제거되는 필드로 `AllowBedrockInvoke`/`AllowModelDiscovery`가 `-` 로 표시되고, Tango statement들은 변경 표시가 없어야 한다.

---

## Task 4: Terraform apply — IAM policy 교체

**목적**: 계획된 변경을 AWS에 반영.

- [ ] **Step 1: apply 실행**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow/infra/terraform
terraform apply /tmp/issue20-tfplan 2>&1 | tee /tmp/issue20-tfapply.log
```

**Expected 마지막 줄**: `Apply complete! Resources: 0 added, 1 changed, 0 destroyed.`

실패 시 Step 3 즉시 실행. 성공 시 Step 2로.

- [ ] **Step 2: 상태 재검증 (plan no-diff)**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow/infra/terraform
terraform plan 2>&1 | grep -E "No changes|will be" | head -5
```

**Expected**: `No changes. Your infrastructure matches the configuration.`

`will be` 라인이 남아있으면 drift 존재 — Task 7 검증 단계에서 원인 분석.

- [ ] **Step 3: (실패 시) 즉시 롤백**

apply 에러 발생 시:

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow
git revert HEAD --no-edit
cd infra/terraform
terraform apply -auto-approve
```

그리고 에러 원인 기록 후 중단.

---

## Task 5: 사후 검증 — IAM 상태 확인

**목적**: 정책이 실제로 축소되었고 Tango 권한은 유지됨을 확인.

- [ ] **Step 1: IAM policy 덤프**

```bash
aws iam get-role-policy \
  --role-name bedrock-claude-bedrock-access \
  --policy-name bedrock-claude-bedrock-invoke \
  --output json 2>&1 | tee /tmp/issue20-post-policy.json
```

- [ ] **Step 2: 제거 확인**

```bash
grep -E '"Sid"' /tmp/issue20-post-policy.json
```

**Expected**: 정확히 4개 Sid 라인만 남음:

```
                "Sid": "AllowTangoS3Read"
                "Sid": "AllowTangoAthenaQuery"
                "Sid": "AllowTangoGlueCatalog"
                "Sid": "AllowAthenaResultsWrite"
```

`AllowBedrockInvoke` 또는 `AllowModelDiscovery` 가 보이면 apply 실패 → Task 4 Step 3 롤백.

- [ ] **Step 3: Tango 권한 스니펫 확인**

```bash
python3 -c "
import json
p = json.load(open('/tmp/issue20-post-policy.json'))
stmts = p['PolicyDocument']['Statement']
print(f'Total statements: {len(stmts)}')
for s in stmts:
    print(f'  - {s[\"Sid\"]}: {s[\"Action\"][:2]}')"
```

**Expected**:

```
Total statements: 4
  - AllowTangoS3Read: ['s3:GetObject', 's3:ListBucket']
  - AllowTangoAthenaQuery: ['athena:StartQueryExecution', 'athena:GetQueryExecution']
  - AllowTangoGlueCatalog: ['glue:GetTable', 'glue:GetTables']
  - AllowAthenaResultsWrite: ['s3:PutObject', 's3:GetObject']
```

---

## Task 6: 사후 검증 — 테스트 Pod에서 실제 경로 확인

**목적**: Claude Code CLI 정상 동작 + 직접 Bedrock 호출 거부 + Tango S3 접근 정상 3가지를 실제 확인.

**전제**: 테스트용 임시 pod를 `claude-sessions` 네임스페이스에 스폰하려면 auth-gateway의 Pod 생성 API를 사용하거나, 수동으로 pod-template 기반 Pod을 apply 한다. 여기서는 수동 apply 방식을 사용 (운영 경로 무영향).

**Files:**
- Create (임시): `/tmp/issue20-test-pod.yaml`

- [ ] **Step 1: 테스트 Pod manifest 생성**

```bash
cat > /tmp/issue20-test-pod.yaml <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: issue20-irsa-probe
  namespace: claude-sessions
  labels:
    app: claude-terminal
    probe: issue-20
spec:
  serviceAccountName: claude-terminal-sa
  restartPolicy: Never
  containers:
    - name: probe
      image: 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest
      command: ["/bin/bash", "-c", "sleep 600"]
      env:
        - name: AWS_REGION
          value: "ap-northeast-2"
EOF

kubectl apply -f /tmp/issue20-test-pod.yaml
kubectl wait --for=condition=Ready pod/issue20-irsa-probe -n claude-sessions --timeout=60s
```

**Expected**: Pod Ready.

- [ ] **Step 2: Bedrock 직접 호출 거부 확인**

```bash
kubectl exec -n claude-sessions issue20-irsa-probe -- \
  aws bedrock-runtime invoke-model \
    --region us-east-1 \
    --model-id global.anthropic.claude-haiku-4-5-20251001-v1:0 \
    --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}' \
    /tmp/out.json 2>&1 | tee /tmp/issue20-bedrock-direct.log
```

**Expected** (stderr 내용에 포함):

```
An error occurred (AccessDeniedException) when calling the InvokeModel operation: ...not authorized to perform: bedrock:InvokeModel...
```

정상 호출되어 응답이 나오면 **변경이 반영되지 않은 것** — IAM eventual consistency(최대 1분) 대기 후 재시도. 여전히 성공 시 Task 4 롤백.

- [ ] **Step 3: Tango 경로 보존 확인 (S3 read)**

```bash
kubectl exec -n claude-sessions issue20-irsa-probe -- \
  aws s3 ls s3://tango-alarm-logs/ --region ap-northeast-2 2>&1 | head -5 | tee /tmp/issue20-tango-s3.log
```

**Expected**: 최소 1개 객체/프리픽스 나열 (AccessDenied 나오면 안 됨). Tango 버킷이 비어있을 경우 `aws s3api head-bucket --bucket tango-alarm-logs` 로 대체:

```bash
kubectl exec -n claude-sessions issue20-irsa-probe -- \
  aws s3api head-bucket --bucket tango-alarm-logs --region ap-northeast-2 2>&1 | tee -a /tmp/issue20-tango-s3.log
```

**Expected**: 빈 출력 (head-bucket 성공은 0 exit code + 빈 stdout).

- [ ] **Step 4: Claude Code CLI proxy 경로 확인**

Pod 내부에서 entrypoint.sh가 이미 `ANTHROPIC_BASE_URL`을 세팅하는지 확인하려면, 테스트 Pod은 단순 sleep이므로 entrypoint.sh가 돌지 않는다. **대안**: auth-gateway Pod 생성 API로 정식 Pod을 스폰하거나, 수동으로 ANTHROPIC_BASE_URL을 설정해 `claude` CLI 호출 검증.

간소화: auth-gateway `/v1/messages` 엔드포인트 직접 호출로 proxy 경로가 살아있는지만 확인:

```bash
kubectl exec -n claude-sessions issue20-irsa-probe -- bash -c '
  curl -sS -X POST http://auth-gateway.platform.svc.cluster.local:8000/v1/messages \
    -H "Content-Type: application/json" \
    -H "x-issue20-probe: test" \
    -d "{}" -o /tmp/resp.txt -w "HTTP %{http_code}\n"
  head -3 /tmp/resp.txt
' 2>&1 | tee /tmp/issue20-proxy.log
```

**Expected** (인증 없이 호출했으므로 401/422 등 정상 오류이면 proxy 자체는 살아있음):

```
HTTP 401   (또는 HTTP 422)
{"detail":"..."}
```

`HTTP 000` / `connection refused` 면 proxy 경로 회귀 → 즉시 롤백(Task 4 Step 3).

- [ ] **Step 5: 테스트 Pod 정리**

```bash
kubectl delete pod issue20-irsa-probe -n claude-sessions
```

---

## Task 7: 검증 리포트 작성

**목적**: Task 1~6 결과를 리포트로 정리.

**Files:**
- Create: `docs/poc/2026-04-14-issue-20-irsa-narrow-verification.md`

- [ ] **Step 1: 로그 파일 수집 상태 확인**

다음 파일들이 모두 존재해야 함:

```bash
ls -l /tmp/issue20-cloudtrail-bedrock-invoke.txt \
      /tmp/issue20-tfplan.log \
      /tmp/issue20-tfapply.log \
      /tmp/issue20-post-policy.json \
      /tmp/issue20-bedrock-direct.log \
      /tmp/issue20-tango-s3.log \
      /tmp/issue20-proxy.log
```

모두 있으면 Step 2.

- [ ] **Step 2: 리포트 파일 작성**

아래 내용으로 `docs/poc/2026-04-14-issue-20-irsa-narrow-verification.md` 작성:

```markdown
# Issue #20 — IRSA Bedrock 권한 축소 검증 리포트

**실행일**: 2026-04-14
**이슈**: #20 Console Pod AWS SDK → HTTP proxy 마이그레이션 (T20)
**변경**: `bedrock-claude-bedrock-invoke` inline policy에서 `AllowBedrockInvoke`, `AllowModelDiscovery` statement 제거
**Spec**: `docs/superpowers/specs/2026-04-14-bedrock-irsa-narrow-design.md`
**ADR**: `docs/decisions/ADR-001-bedrock-irsa-narrow.md`

## 사전 검증 — CloudTrail (최근 7일)

`claude-terminal-sa` IRSA 세션의 직접 Bedrock 호출 건수:

- `InvokeModel`: <N>건
- `InvokeModelWithResponseStream`: <M>건

원본 로그: `/tmp/issue20-cloudtrail-bedrock-invoke.txt` (Task 1 결과 붙여넣기)

판정: **✅ 진행 가능** (합계 0건) / **⚠ 중단 필요** (≥1건)

## Terraform plan 검토

변경된 리소스: `aws_iam_role_policy.bedrock_invoke` in-place update (1개)

diff 요약 (`/tmp/issue20-tfplan.log` 기반):
- 제거: `AllowBedrockInvoke`, `AllowModelDiscovery` 2개 statement
- 유지: `AllowTangoS3Read`, `AllowTangoAthenaQuery`, `AllowTangoGlueCatalog`, `AllowAthenaResultsWrite` 4개

## Terraform apply 결과

`Apply complete! Resources: 0 added, 1 changed, 0 destroyed.`

사후 `terraform plan` 재실행: `No changes. Your infrastructure matches the configuration.` (drift 없음)

## 사후 IAM 상태

`aws iam get-role-policy --role-name bedrock-claude-bedrock-access --policy-name bedrock-claude-bedrock-invoke`:

Statement 4개:
- AllowTangoS3Read
- AllowTangoAthenaQuery
- AllowTangoGlueCatalog
- AllowAthenaResultsWrite

(Bedrock 관련 Sid 부재 확인)

## 실제 경로 검증 (테스트 Pod)

### Bedrock 직접 호출 차단

```
<Task 6 Step 2 /tmp/issue20-bedrock-direct.log 핵심 라인 붙여넣기>
```

→ `AccessDeniedException ... not authorized to perform: bedrock:InvokeModel` ✅

### Tango S3 경로 보존

```
<Task 6 Step 3 /tmp/issue20-tango-s3.log 핵심 라인 붙여넣기>
```

→ 접근 정상 ✅

### Bedrock AG proxy 경로 reachability

```
<Task 6 Step 4 /tmp/issue20-proxy.log HTTP code 라인 붙여넣기>
```

→ HTTP 응답 수신 = proxy 서비스 가용 ✅

## 결론

본 이슈의 T20 마지막 서브태스크(NetworkPolicy egress 차단) 대안으로 IAM policy narrow 접근 완료. C.1 스코프 전체 DoD 충족.

## Deferred (Phase 2)

- FQDN-level egress 차단 (squid 도입)
- api.anthropic.com 직접 호출 차단 (현 시점 NetworkPolicy egress `443 anywhere`로 허용 중)
- Tango 경로 S3/Athena VPC endpoint 전환
```

실제 값(`<N>건`, 로그 라인 등)은 Step 1에서 확인한 파일 내용으로 치환.

- [ ] **Step 3: 커밋**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow
git add docs/poc/2026-04-14-issue-20-irsa-narrow-verification.md
git commit -m "docs(phase1-backlog/#20): IRSA 축소 사전/사후 검증 리포트 추가

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: ADR-001 작성

**목적**: 향후 회귀 방지용 의사결정 문서.

**Files:**
- Create: `docs/decisions/ADR-001-bedrock-irsa-narrow.md`

- [ ] **Step 1: 디렉토리 생성**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow
mkdir -p docs/decisions
```

- [ ] **Step 2: ADR 파일 작성**

아래 내용으로 `docs/decisions/ADR-001-bedrock-irsa-narrow.md` 작성:

```markdown
# ADR-001: 사용자 Pod IRSA에서 Bedrock 권한 제거

- **Status**: Accepted
- **Date**: 2026-04-14
- **Issue**: [#20 Console Pod AWS SDK → HTTP proxy 마이그레이션 (T20)](https://github.com/cation98/bedrock-ai-agent/issues/20)
- **Spec**: `docs/superpowers/specs/2026-04-14-bedrock-irsa-narrow-design.md`

## Context

T20 목적은 사용자 Pod의 Bedrock 호출 경로를 **Bedrock AG proxy로 단일화**하여 사용량 추적·audit·rate-limit 중앙화를 달성하는 것이다. 완료된 부분:

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

- Tango use case 사용자가 **의도치 않게** `aws bedrock-runtime`로 LLM을 붙이려던 실험 코드가 있다면 실패 (현재 0건 확인)
- 신규 AI 서비스 추가 시 권한 재검토 필요 (좋은 제약)

### 리스크

- IAM eventual consistency(최대 1분) — apply 직후 Pod 호출 테스트 시 과도기 발생 가능
- Terraform과 AWS 실 상태 drift 방지 — apply 후 `terraform plan` 재검증으로 관리

### Deferred (이 결정으로 해소되지 않음 → Phase 2 squid 필요)

1. **FQDN-level egress 차단 부재** — 사용자가 자체 `ANTHROPIC_API_KEY` 설정 시 `api.anthropic.com` 직접 호출 차단 불가
2. **기타 AI API 엔드포인트** (OpenAI, Gemini 등) 직접 호출 차단 불가
3. **Tango 경로의 S3/Athena는 NAT 경유 public endpoint** — VPC endpoint 전환은 별도 과제

## 실행 기록

- 변경 커밋: (Task 2 Step 5 SHA)
- Terraform apply 기록: `docs/poc/2026-04-14-issue-20-irsa-narrow-verification.md`
- 롤백 경로: `git revert <commit-sha> && terraform apply`

## 재검토 트리거

다음 중 하나 발생 시 이 결정을 재검토:
- Phase 2 squid egress gateway 배포 시 (IAM 제약 유지 vs 네트워크 레벨 이관 판단)
- Tango 이외의 use case가 Bedrock 직접 호출을 요구할 때 (새로운 SA 분리 검토)
- AWS Bedrock이 계정 단위 SCP로 제어 가능해지면 (IAM role-level 대신 상위 경계 이동 검토)
```

실제 커밋 SHA는 Step 3에서 채운다.

- [ ] **Step 3: Task 2 Step 5에서 얻은 commit SHA를 ADR에 주입**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow
IAM_COMMIT=$(git log --oneline --grep="#20.*IRSA.*Bedrock statement" | awk '{print $1}' | head -1)
echo "iam.tf commit SHA: $IAM_COMMIT"
```

Edit 도구로 ADR 문서의 `(Task 2 Step 5 SHA)` 문자열을 실제 SHA로 치환.

- [ ] **Step 4: 커밋**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow
git add docs/decisions/ADR-001-bedrock-irsa-narrow.md
git commit -m "docs(phase1-backlog/#20): ADR-001 IRSA Bedrock narrow 의사결정 기록

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: main 머지 + 이슈 close

**목적**: worktree 브랜치를 main에 반영하고 이슈 종결.

- [ ] **Step 1: PR 생성**

```bash
cd /Users/cation98/Project/bedrock-ai-agent/.worktrees/issue-20-irsa-narrow
git push -u origin phase1-backlog/#20-irsa-narrow

gh pr create --title "refactor(#20): IRSA 사용자 Pod Bedrock 권한 제거" --body "$(cat <<'BODY'
## Summary
- 이슈 #20 마지막 서브태스크(NetworkPolicy egress 차단) 대안으로 IAM narrow 채택
- `bedrock-claude-bedrock-invoke`에서 `AllowBedrockInvoke`, `AllowModelDiscovery` 2개 statement 제거
- Tango use case S3/Athena/Glue 4개 statement 유지
- ADR-001, 검증 리포트 동봉

## Test plan
- [x] Task 1 — CloudTrail 사전 검증 (최근 7일 직접 호출 0건 확인)
- [x] Task 3 — Terraform plan: 1 changed (bedrock_invoke policy only)
- [x] Task 4 — Terraform apply 성공, drift 없음
- [x] Task 5 — IAM policy 사후 상태: Tango 4개 statement만 존재
- [x] Task 6 — 테스트 Pod: Bedrock 직접 호출 AccessDenied + Tango S3 정상 + proxy reachability 확인

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

- [ ] **Step 2: PR 머지 (사용자 리뷰 후)**

```bash
gh pr merge --squash --delete-branch
```

**Note**: 사용자가 직접 승인하는 것이 기본. 자동 머지는 기본적으로 하지 않는다.

- [ ] **Step 3: 이슈 #20 close**

```bash
gh issue close 20 --comment "$(cat <<'COMMENT'
## 종결 (2026-04-14)

**결론: ✅ T20 완료**

NetworkPolicy egress 차단 서브태스크는 **IAM policy narrow 접근(C.1)으로 대체**하여 동일 목적 달성.

### 처리 내역

- `bedrock-claude-bedrock-invoke` policy에서 Bedrock action statement 2개 제거
- Tango use case S3/Athena/Glue 권한 보존
- 사전 CloudTrail(7일): 직접 호출 0건 확인
- 사후 테스트 Pod: `aws bedrock-runtime invoke-model` → `AccessDenied`, Tango S3 정상, proxy reachability OK

### 문서

- ADR: `docs/decisions/ADR-001-bedrock-irsa-narrow.md`
- Spec: `docs/superpowers/specs/2026-04-14-bedrock-irsa-narrow-design.md`
- 검증 리포트: `docs/poc/2026-04-14-issue-20-irsa-narrow-verification.md`

### Phase 2로 이관된 과제

- FQDN-level egress 차단 (squid 도입)
- api.anthropic.com / 기타 AI 엔드포인트 직접 호출 차단
- Tango 경로 S3/Athena VPC endpoint 전환
COMMENT
)"
```

---

## Task 10: worktree 정리 (finishing-a-development-branch)

- [ ] **Step 1: worktree 제거**

```bash
cd /Users/cation98/Project/bedrock-ai-agent
git worktree list
git worktree remove .worktrees/issue-20-irsa-narrow
```

- [ ] **Step 2: 로컬 브랜치 정리 (PR 머지로 삭제된 경우 자동)**

```bash
git branch -D phase1-backlog/#20-irsa-narrow 2>/dev/null || echo "already pruned"
```

- [ ] **Step 3: 최종 상태 확인**

```bash
cd /Users/cation98/Project/bedrock-ai-agent
git status       # clean
git log --oneline -5   # 머지된 커밋 확인
```

---

## Self-Review (plan 작성 후 점검)

**1. Spec coverage**:
- 목표 2개 (IRSA Bedrock 제거, Tango 보존) → Task 2 / Task 5 / Task 6 ✅
- 사전 검증(CloudTrail) → Task 1 ✅
- 사후 검증(IAM state, Pod 경로) → Task 5, 6 ✅
- ADR 작성 → Task 8 ✅
- 검증 리포트 → Task 7 ✅
- 롤백 경로 → Task 4 Step 3 ✅
- 성공 판정 기준 6개 → Task 3 (plan), 4 (apply), 5 (IAM state), 6 (pod test) ✅

**2. Placeholder scan**: TBD/TODO 없음, "appropriate error handling" 없음, 모든 command 구체. ✅

**3. Type consistency**: 파일 이름, policy 이름, role 이름 전 task 일관. Task 2 Edit 블록과 Task 5 expected output 모두 동일한 Sid 철자 사용. ✅
