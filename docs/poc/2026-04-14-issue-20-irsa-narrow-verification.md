# Issue #20 — IRSA Bedrock 권한 축소 검증 리포트

**실행일**: 2026-04-14
**이슈**: [#20 Console Pod AWS SDK → HTTP proxy 마이그레이션 (T20)](https://github.com/cation98/bedrock-ai-agent/issues/20)
**변경**: `bedrock-claude-bedrock-invoke` inline policy에서 `AllowBedrockInvoke`, `AllowModelDiscovery` statement 제거
**Spec**: `docs/superpowers/specs/2026-04-14-bedrock-irsa-narrow-design.md`
**ADR**: `docs/decisions/ADR-001-bedrock-irsa-narrow.md`
**Plan**: `docs/superpowers/plans/2026-04-14-bedrock-irsa-narrow.md`

---

## 사전 검증 — CloudTrail (최근 7일)

`claude-terminal-sa` IRSA 세션의 직접 Bedrock 호출 건수 (region: ap-northeast-2):

- `InvokeModel`: 0건
- `InvokeModelWithResponseStream`: 0건
- **Total: 0건** → ✅ PROCEED 게이트 통과

로그: `/tmp/issue20-cloudtrail-bedrock-invoke.txt` — 파일 크기 0 bytes (빈 결과셋). CloudTrail Insights 쿼리가 일치하는 이벤트를 반환하지 않음. 직접 Bedrock 호출 없음 확인.

---

## Terraform 적용 — targeted apply (-target)

`terraform plan`에서 10개 리소스 변경이 감지되었으나 9개는 사전 drift (본 PR 범위 외). mindbase memory `lesson-terraform-apply-drift-safety` 교훈에 따라 `-target=aws_iam_role_policy.bedrock_invoke`로 범위 한정 apply 선택.

**Targeted plan** (`/tmp/issue20-tfplan-targeted.log`):

```
# aws_iam_role_policy.bedrock_invoke will be updated in-place
~ policy = jsonencode(
    ~ {
        ~ Statement = [
            - { Sid = "AllowBedrockInvoke", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], ... }
            - { Sid = "AllowModelDiscovery", Action = ["bedrock:ListFoundationModels", "bedrock:GetFoundationModel"], ... }
              { Sid = "AllowTangoS3Read", ... }  # (3 unchanged elements hidden)
          ]
      }
  )
Plan: 0 to add, 1 to change, 0 to destroy.
```

**Apply 결과** (`/tmp/issue20-tfapply.log`):

```
aws_iam_role_policy.bedrock_invoke: Modifying... [id=bedrock-claude-bedrock-access:bedrock-claude-bedrock-invoke]
aws_iam_role_policy.bedrock_invoke: Modifications complete after 1s
Apply complete! Resources: 0 added, 1 changed, 0 destroyed.
```

**사후 재 plan**: `-target` 재실행 시 `No changes. Your infrastructure matches the configuration.` (대상 리소스 drift 없음)

9개 사전 drift 항목 (별도 이슈로 추적 필요):

- `aws_ecr_lifecycle_policy.bedrock_ag` (create)
- `aws_eks_node_group.burst_workers` / `dedicated` / `ingress` / `main` / `system` (create 또는 update)
- `aws_iam_role.bedrock_ag_access` / `aws_iam_role_policy.bedrock_ag_invoke` (create)
- `aws_kms_key.s3_vault` (update: deletion_window 14→30)

---

## 사후 IAM 상태

`aws iam get-role-policy --role-name bedrock-claude-bedrock-access --policy-name bedrock-claude-bedrock-invoke` (`/tmp/issue20-post-policy.json`):

Statement 4개 (Tango use case만) — 아래 Sid 전체 목록:

| Sid | Actions | Resource |
|-----|---------|----------|
| `AllowTangoS3Read` | `s3:GetObject`, `s3:ListBucket`, `s3:GetBucketLocation` | `arn:aws:s3:::tango-alarm-logs`, `arn:aws:s3:::tango-alarm-logs/*` |
| `AllowTangoAthenaQuery` | `athena:StartQueryExecution`, `athena:GetQueryExecution`, `athena:GetQueryResults`, `athena:StopQueryExecution`, `athena:GetWorkGroup` | `arn:aws:athena:ap-northeast-2:680877507363:workgroup/primary` |
| `AllowTangoGlueCatalog` | `glue:GetTable`, `glue:GetTables`, `glue:GetDatabase`, `glue:GetDatabases`, `glue:GetPartitions` | `arn:aws:glue:ap-northeast-2:680877507363:catalog`, `database/tango_logs`, `table/tango_logs/*` |
| `AllowAthenaResultsWrite` | `s3:PutObject`, `s3:GetObject`, `s3:AbortMultipartUpload` | `arn:aws:s3:::tango-alarm-logs/athena-results/*` |

**Bedrock 관련 Sid 부재 확인**: `AllowBedrockInvoke`, `AllowModelDiscovery` 모두 제거됨. policy 파일에 `bedrock:` 액션 0건.

---

## 실제 경로 검증 (테스트 Pod `issue20-irsa-probe`)

테스트 Pod은 `claude-sessions` 네임스페이스에서 `claude-terminal-sa` ServiceAccount로 스폰. 검증 완료 후 삭제.

### Bedrock 직접 호출 차단 ✅

로그: `/tmp/issue20-bedrock-direct.log`

```
aws: [ERROR]: An error occurred (AccessDeniedException) when calling the InvokeModel operation:
User: arn:aws:sts::680877507363:assumed-role/bedrock-claude-bedrock-access/botocore-session-1776115781
is not authorized to perform: bedrock:InvokeModel on resource:
arn:aws:bedrock:us-east-1:680877507363:inference-profile/global.anthropic.claude-haiku-4-5-20251001-v1:0
because no identity-based policy allows the bedrock:InvokeModel action
EXIT:0
```

→ IAM narrowing이 실제 런타임에 반영됨 확인. `aws bedrock-runtime invoke-model` 명령이 `AccessDeniedException`으로 즉시 거부.

### Tango S3 경로 보존 ✅

로그: `/tmp/issue20-tango-s3.log`

```
$ aws s3 ls s3://tango-alarm-logs/
                           PRE athena-results/
                           PRE bedrock-logs/
                           PRE raw/
                           PRE test/
```

`head-bucket` 결과:
```json
{
    "BucketArn": "arn:aws:s3:::tango-alarm-logs",
    "BucketRegion": "ap-northeast-2",
    "AccessPointAlias": false
}
```

→ S3 ListBucket + head-bucket 모두 성공. Tango use case 영향 없음.

### Bedrock AG proxy reachability ✅

로그: `/tmp/issue20-proxy.log`

```
{"detail":"Not authenticated"}
```

→ `curl http://auth-gateway.platform.svc.cluster.local/v1/messages` (Service port 80 → 8000) 에서 `HTTP 401` 수신. proxy 서비스 가용. (401은 JWT 없이 호출했으므로 정상 응답. 참고: exit code 137은 kubectl exec 세션 종료에 의한 SIGKILL이며 응답 수신 후 발생)

---

## 결론

이슈 #20 T20의 마지막 서브태스크(NetworkPolicy egress 차단)를 **IAM narrow (C.1)**으로 대체하여 동일 목적 달성. 사용자 Pod에서 Bedrock 직접 호출은 IAM 레벨에서 차단되고, Tango use case는 무영향.

| 검증 항목 | 결과 |
|----------|------|
| CloudTrail 7일 직접 호출 0건 | ✅ PROCEED 게이트 통과 |
| Terraform targeted apply 1 changed | ✅ |
| IAM policy Bedrock Sid 제거 확인 | ✅ |
| `bedrock:InvokeModel` → AccessDeniedException | ✅ |
| Tango S3 ListBucket 보존 | ✅ |
| auth-gateway proxy HTTP 401 응답 | ✅ |

---

## 부수 발견 (별도 추적 필요)

**pre-existing NetworkPolicy 결함** — `claude-sessions/allow-user-pod-traffic`의 egress 규칙에서 `auth-gateway` 대상 port 8000 엔트리가 bare `podSelector`만 사용 (`namespaceSelector` 미포함) → cross-namespace 매칭 실패. 사용자 Pod이 `auth-gateway.platform.svc.cluster.local:8000`으로 direct 접근 시 타임아웃. Service 경유(port 80)는 동작. 이번 IRSA narrowing과 무관한 사전 결함이나 Phase 1 완료 전 별도 이슈로 추적 권장.

---

## 이관된 과제 (Phase 2)

- FQDN-level egress 차단 (squid 도입)
- `api.anthropic.com` 직접 호출 차단 (현재 NetworkPolicy egress `443 anywhere`로 허용 중)
- Tango 경로 S3/Athena 호출의 VPC endpoint 전환
- 9개 사전 Terraform state drift 해소 (별도 이슈)
- NetworkPolicy `allow-user-pod-traffic` port 8000 cross-namespace 수정 (위 부수 발견)
