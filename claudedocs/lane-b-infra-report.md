# Lane B Infra Report — IRSA + H2 Manifest 수정

**작성일**: 2026-04-12  
**담당**: k8s teammate  
**관련 이슈**: claudedocs/I2-k8s-audit.md (F1, F2)  
**관련 설계**: docs/plans/2026-04-12-onlyoffice-ai-integration-design.md (Lane B)

---

## 변경 요약

### B1: IRSA — auth-gateway Bedrock 권한 연결

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `infra/terraform/iam.tf` | 수정 | `auth_gateway_bedrock_invoke` 정책에 Converse API 액션 추가 |
| `infra/k8s/platform/auth-gateway.yaml` | 추가 | `platform-admin-sa` ServiceAccount 매니페스트 신규 생성 |

### B2: H2 env — onlyoffice.yaml 누락 환경변수 수정

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `infra/k8s/platform/onlyoffice.yaml` | 수정 | F1+F2 env 4개 + lifecycle.postStart 추가 |

---

## 변경 상세 diff

### B1-a: infra/terraform/iam.tf

`aws_iam_role_policy.auth_gateway_bedrock_invoke` — AllowBedrockInvoke 액션 확장:

```diff
  Action = [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream",
+   "bedrock:Converse",       # boto3 converse() API IAM 액션
+   "bedrock:ConverseStream", # boto3 converse_stream() API IAM 액션
  ]
```

**이유**: Lane A 구현이 `boto3.client('bedrock-runtime').converse_stream()`을 사용. AWS는 Converse API를 InvokeModel과 별도 IAM 액션으로 분리함. 기존 `InvokeModelWithResponseStream`만으로는 `converse_stream()` 호출 시 AccessDenied 가능.

### B1-b: infra/k8s/platform/auth-gateway.yaml

`platform-admin-sa` ServiceAccount 매니페스트 신규 추가:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: platform-admin-sa
  namespace: platform
  annotations:
    eks.amazonaws.com/role-arn: "arn:aws:iam::680877507363:role/bedrock-claude-auth-gateway-bedrock"
```

**이유**: 기존에는 `platform-admin-sa`가 K8s 매니페스트로 정의되지 않았음(rbac.yaml, auth-gateway.yaml에서만 참조).
Phase 0 drift: SA가 AWS CLI로 직접 관리된 `bedrock-claude-platform-admin` 역할을 가리키고 있었음.  
Phase 1 Option A: Terraform 관리 역할 `bedrock-claude-auth-gateway-bedrock`으로 annotation 일원화.

### B2: infra/k8s/platform/onlyoffice.yaml

F1 + F2 수정 — 로컬 개발 매니페스트(`infra/local-dev/07-onlyoffice.yaml`)와 동일하게 맞춤:

```diff
  env:
  - name: JWT_ENABLED
    value: "true"
  - name: JWT_SECRET
    valueFrom: ...
+
+ # F1: 사설 IP 다운로드 허용
+ - name: ALLOW_PRIVATE_IP_ADDRESS
+   value: "true"
+
+ # F2-a: JWT Inbox 검증 비활성화
+ - name: JWT_INBOX_ENABLED
+   value: "false"
+
+ # F2-b: local.json 직접 오버라이드 (ENV 단독 적용 불안정 보완)
+ - name: ONLYOFFICE_DOCS_PARAMS
+   value: '{"services":{"CoAuthoring":{"token":{"enable":{"request":{"inbox":false}}}}}}'

+ # F2-c: postStart — local.json 직접 패치
+ lifecycle:
+   postStart:
+     exec:
+       command: [bash, -c, "sleep 5 && python3 -c '...' && supervisorctl restart ds:docservice 2>/dev/null || true"]
```

---

## terraform plan 출력 하이라이트

```
# aws_iam_role_policy.auth_gateway_bedrock_invoke will be created
+ resource "aws_iam_role_policy" "auth_gateway_bedrock_invoke" {
    + policy = jsonencode({
        Statement = [{
          Action = [
            "bedrock:InvokeModel",
            "bedrock:InvokeModelWithResponseStream",
            "bedrock:Converse",
            "bedrock:ConverseStream",
          ]
          Effect   = "Allow"
          Resource = ["arn:aws:bedrock:*::foundation-model/anthropic.claude-*", ...]
          Sid      = "AllowBedrockInvoke"
        }, ...]
    })
}

Changes to Outputs:
  + auth_gateway_bedrock_role_arn = (known after apply)

Plan: 14 to add, 5 to change, 5 to destroy.
```

**주의 — plan 범위 (14 add / 5 change / 5 destroy)**:  
Lane B 직접 변경: `auth_gateway_bedrock_invoke` policy 1건(create).  
나머지 13 add / 5 change / 5 destroy는 기존 terraform state drift(EKS nodegroup, security group, ECR repo 등)로 이번 Lane B 변경과 무관. apply 전 팀장 검토 필요.

---

## kubectl dry-run 결과

```bash
$ kubectl --context docker-desktop apply -f infra/k8s/platform/auth-gateway.yaml --dry-run=client
serviceaccount/platform-admin-sa configured (dry run)   ✅
namespace/platform configured (dry run)                 ✅
deployment.apps/auth-gateway configured (dry run)       ✅
service/auth-gateway configured (dry run)               ✅

$ kubectl --context docker-desktop apply -f infra/k8s/platform/onlyoffice.yaml --dry-run=client
persistentvolumeclaim/onlyoffice-data-pvc created (dry run)   ✅
deployment.apps/onlyoffice configured (dry run)               ✅
service/onlyoffice configured (dry run)                       ✅
```

모든 리소스 파싱/유효성 검사 통과. 에러 없음.

---

## apply 영향 분석

### terraform apply 시 영향

| 리소스 | 변경 유형 | 예상 영향 |
|--------|----------|----------|
| `aws_iam_role_policy.auth_gateway_bedrock_invoke` | Create (새 정책 연결) | 무중단. 기존 SA의 실제 권한 소스는 `bedrock-claude-platform-admin`이므로 이 정책 추가는 병렬 적용 |
| 기타 14 add / 5 change / 5 destroy | 기존 drift | 별도 검토 필요 (EKS 노드그룹 변경 포함) |

**IRSA 전환 완료 순서** (terraform apply 후):
1. `terraform output auth_gateway_bedrock_role_arn` → ARN 확인
2. `auth-gateway.yaml`의 SA annotation ARN이 일치하는지 확인 (현재 하드코딩: `arn:aws:iam::680877507363:role/bedrock-claude-auth-gateway-bedrock`)
3. `kubectl apply -f infra/k8s/platform/auth-gateway.yaml` → SA annotation 반영
4. auth-gateway rollout restart → 새 IRSA 자격증명으로 재기동

### kubectl apply 시 영향 (onlyoffice.yaml)

| 변경 | 영향 |
|------|------|
| env 4개 추가 | OO Pod 재시작 트리거. 재시작 중 30-60초 편집 불가 (replicas=1) |
| lifecycle.postStart 추가 | OO 기동 시 5-15초 추가 지연 (sleep 5 + python3 패치 + supervisorctl 재시작) |

**OO 재시작 예상 소요**: 총 liveness probe initialDelaySeconds(60초) + postStart(~15초) = 약 75초.

---

## 롤백 계획

### terraform 롤백

`auth_gateway_bedrock_invoke` 정책은 새로 생성되므로 롤백 시 단순히 새 액션 2개를 제거:
```bash
# 긴급 롤백: terraform destroy 대신 정책 수동 업데이트
aws iam get-role-policy --role-name bedrock-claude-auth-gateway-bedrock \
  --policy-name bedrock-claude-auth-gateway-bedrock-invoke
# → policy에서 bedrock:Converse, bedrock:ConverseStream 제거 후 put-role-policy
```

IRSA 전환 롤백 (SA annotation 복원):
```bash
kubectl annotate sa platform-admin-sa -n platform \
  eks.amazonaws.com/role-arn=arn:aws:iam::680877507363:role/bedrock-claude-platform-admin \
  --overwrite
```

### kubectl 롤백 (onlyoffice.yaml)

```bash
git revert HEAD  # or: git checkout origin/main -- infra/k8s/platform/onlyoffice.yaml
kubectl apply -f infra/k8s/platform/onlyoffice.yaml
```

OO Pod 재시작 시 편집 세션 데이터는 EFS PVC(`/var/lib/onlyoffice`)에 영속화되므로 롤백 시 데이터 손실 없음.

---

## 미해결 / 후속 작업

| # | 항목 | 우선순위 | 담당 |
|---|------|---------|------|
| 1 | terraform plan에 14 add / 5 change / 5 destroy — Lane B 외 drift 항목 검토 필요 | 🟡 HIGH | 사용자 승인 |
| 2 | SA annotation ARN 하드코딩 → `terraform output` 자동 주입 파이프라인 고려 | 🟢 LOW | 후속 |
| 3 | F3: `/var/www/onlyoffice/Data` EFS 볼륨 마운트 (I2 audit 중기 항목) | 🟡 MEDIUM | Lane C 또는 별도 |
| 4 | F4: initContainer Noto CJK 폰트 설치 (I2 audit 중기 항목) | 🟡 MEDIUM | Lane C 또는 별도 |
| 5 | F5: 메모리 limit 5-6Gi 상향 검토 | 🟢 LOW | 모니터링 후 |
