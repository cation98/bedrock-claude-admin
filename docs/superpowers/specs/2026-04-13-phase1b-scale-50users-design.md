# Phase 1b — 50명 상시 운용 Scale 대응 설계

**Date**: 2026-04-13
**Author**: Phase 1b planning (Claude Opus 4.6 + cation98)
**Session**: phase0-merge-phase1-kickoff-2026-04-12 (mindbase 5887c1a2, continues)
**Base**: main after Phase 1a merge (사용자 승인 후)
**Parent sessions**: phase0-blockers-main-check-2026-04-12 (fdf2d2b5)

---

## 1. 목표 (Goal)

팀장 50명 상시 운용 대응을 위한 EKS 스케일 재조정 + Phase 1a main_tls HA cluster cutover 정리 + 50명 부하 Locust SLO baseline 확보. 기능 완성도(CP-20/22, T20 refresh daemon, issue-jwt 등)는 **Phase 1c로 이관**.

## 2. 배경 (Context)

### Phase 1a 선행 상태
- Phase 1a 보안 hardening 7항목 구현 완료 (worktree `feat/phase1a-security-hardening @cadd573`, 21 commits, main 미 merge)
- ElastiCache `main_tls` HA cluster 신규 생성 + 모든 Deployment(auth-gateway/usage-worker/openwebui-pipelines) 전환 완료
- Phase 0 standalone cluster `aws_elasticache_cluster.main`는 **동시 운영 중** (비용 중복)
- 9/9 security + 10/10 drift + 91 auth-gateway + 15 ops/export 회귀 PASS

### 선행 조건
- **Phase 1a → main merge 사용자 승인 필요** — Phase 1b는 Phase 1a 위에 빌드
- Phase 1a merge 후 HEAD에서 Phase 1b branch 분기

## 3. 범위 (Scope)

### In scope (Phase 1b)
1. Phase 0 ElastiCache standalone cluster destroy (cost 절감 + 단일 운영)
2. EKS nodegroup main sizing 상향 (desired 4→6, max 6→12)
3. Burst-workers nodegroup 신규 (spot m5.xlarge, desired 0 / max 4)
4. auth-gateway HPA 재조정 (minReplicas 2, maxReplicas 4, CPU 70% / Memory 80%)
5. auth-gateway PDB minAvailable 1 재검증
6. Locust 50 users 5분 부하 검증 + SLO baseline

### Out of scope (Phase 1c로 이관)
- CP-20 budget_gate 실체 구현
- CP-22 usage_emit 실체 연결
- /auth/issue-jwt 엔드포인트
- T20 background token refresh daemon
- FileAuditAction Enum 구현
- Skills governance 스키마 + SoD UI
- DEFAULT_USER_ROLE + model access_control
- Locust cookie 인증 전환
- psql CI image / psycopg2
- auth_gateway_bedrock SA annotation IRSA drift 정리
- RDS instance class 업그레이드 (실 부하 관찰 후 결정)
- ElastiCache `cache.t3.small → medium` (실 부하 관찰 후 결정)
- usage-worker HPA 도입 (실 consume lag 관찰 후)

## 4. 아키텍처 영향

```
[Infra — Terraform changes]
  aws_elasticache_cluster.main (Phase 0 standalone)
    → DESTROY (main_tls 단독 운영)

  aws_eks_node_group.main
    → desired_size 4 → 6
    → max_size    6 → 12

  aws_eks_node_group.burst_workers (신규)
    → desired 0, max 4
    → capacity_type = SPOT
    → instance_types = ["m5.xlarge", "m5a.xlarge"]
    → labels: role=burst
    → taints: [] 또는 soft (필요 시 PreferNoSchedule)

[K8s config]
  infra/k8s/platform/auth-gateway-hpa.yaml (신규 or 수정)
    → minReplicas: 2, maxReplicas: 4
    → targetCPU: 70%, targetMemory: 80%

  infra/k8s/platform/auth-gateway.yaml (PDB 섹션 검증)
    → minAvailable: 1 유지 (4 replica scale에서도 안전)

[No code changes]
  Python/앱 코드 변경 없음 — 인프라 layer 조정만
```

## 5. 구현 전략 (Approach A — Sequential)

Sequential subagent-driven. Task 0(선행) → 1 → 2 → 3 → 4 → 5 → 6 순.
각 Task 후 spec compliance + code quality two-stage review.

### Task 구성

- **Task 0 (선행)**: Phase 1a main merge 확인 + Phase 1b worktree 생성
- **Task 1**: ElastiCache standalone cluster destroy
- **Task 2**: EKS nodegroup main sizing 상향
- **Task 3**: Burst-workers nodegroup 신규 (spot)
- **Task 4**: auth-gateway HPA + PDB 재조정
- **Task 5**: Locust 50 users 부하 검증 (SLO 판정)
- **Task 6**: 합동 검증 보고서 + main merge 준비 (실행은 사용자 승인 후)

### 팀 구성
- devops: 주 구현자 (sequential)
- security: spot-check (rediss + KMS)
- qa: Locust 실행 + SLO 판정

### 예상 기간
3-4일.

## 6. 격리 전략

- 신규 worktree `.worktrees/feat-phase1b-scale-50users`
- 신규 branch `feat/phase1b-scale-50users` (main 기반, Phase 1a merge 이후 HEAD에서 분기)
- Phase 1b 완료 후 사용자 승인 시 main merge

## 7. 테스트/검증 전략

### Unit / Regression
Phase 1b는 Python 코드 변경 없음. auth-gateway suite baseline 유지 확인만:

```bash
cd auth-gateway
pytest tests/test_viewers.py tests/test_k8s_service.py tests/test_shared_mounts_auth.py \
  tests/test_jwt_replay_protection.py tests/test_auth_jwt_phase0.py \
  tests/test_docs_hidden.py tests/test_www_authenticate_bearer.py \
  tests/test_deterministic_kid.py -q
```
기대: 91 passed (Phase 1a 마감 상태 유지).

### Terraform plan 검증
각 Task Apply 전 plan diff 명시적 검토:

| Task | 기대 diff |
|------|----------|
| 1 | 0 add / 0 change / 1 destroy (standalone cluster only) |
| 2 | 0 add / 1 change / 0 destroy (nodegroup in-place) |
| 3 | 1 add / 0 change / 0 destroy (burst-workers 신규) |

예상 외 destroy/replace 있으면 STOP + 사용자 보고.

### EKS 노드 검증
각 Task 후:
```bash
kubectl get nodes -o wide --show-labels | grep -E "role=main|role=burst|role=system|role=ingress"
```

### HPA 실 동작 검증 (Task 5)
Locust 부하 중 병렬 관측:
```bash
kubectl get hpa auth-gateway -n platform -w
kubectl get pod -n platform -l app=auth-gateway -w
```
기대: 2 → 3~4 자동 scale-up.

### Locust SLO 판정

```bash
LOCUST_TEST_TOKEN=$TEST_USER_TOKEN \
  locust -f tests/load/locustfile.py \
  --host https://claude.skons.net \
  --users 50 --spawn-rate 5 --run-time 5m --headless
```

| 항목 | SLO | 기록 |
|------|-----|------|
| p95 | < 150ms | 실측 |
| 에러율 | < 1% | % |
| RPS | ~250 | 달성률 |
| HPA scale | 2→3+ | 이벤트 로그 |

FAIL 시 조치:
- p95 > 150ms → nodegroup main max 12 → 16 상향, 재실행
- 에러율 > 1% → auth-gateway 로그 + pod restart 분석
- HPA scale 안 됨 → metrics-server 설치 + HPA behavior 점검

### Security spot-check
Phase 1a 9 체크 중 2건만 재실행:
1. rediss transit (main_tls 단독 연결 확인)
2. KMS rotation (변경 없어야)

### Rollback 전략

| Task | 롤백 방법 |
|------|----------|
| 1 | `terraform import aws_elasticache_cluster.main ...` + tf 블록 복구 |
| 2 | tf 이전 상태(desired 6→4, max 12→6) apply |
| 3 | `terraform destroy -target=aws_eks_node_group.burst_workers` |
| 4 | `kubectl delete hpa auth-gateway` or revert commit |

Task 1 실패 시 복구 복잡도 가장 높음 → plan 검토 엄격히.

## 8. 완료 게이트 (Acceptance criteria)

1. [ ] `aws elasticache describe-cache-clusters` 에 Phase 0 standalone 미존재
2. [ ] `kubectl get nodes -l role=main` 6대 Ready
3. [ ] `kubectl get nodes -l role=burst` 0대 (desired=0)
4. [ ] `kubectl get hpa auth-gateway -n platform` min=2/max=4
5. [ ] Locust 50 users 5분: p95 < 150ms, 에러율 < 1%
6. [ ] 부하 중 HPA scale-up 이벤트 기록
7. [ ] auth-gateway 91 + ops/export 15 회귀 없음
8. [ ] `docs/qa/phase1b-joint-report.md` 6 섹션
9. [ ] security spot-check 2건 PASS
10. [ ] main merge 준비(보고서 + 머지 절차 문서화), 실행 보류 (사용자 승인 대기)

## 9. 리스크 및 대응

| 리스크 | 대응 |
|--------|------|
| Task 1 standalone destroy 실수로 main_tls 영향 | plan target 검증 + aws CLI 이전 재확인 |
| nodegroup 상향 시 spot capacity 부족 | On-demand fallback 허용 (mixed instance policy) |
| HPA scale-up 안 됨 (metrics-server 없음) | Task 5 전 metrics-server 설치 확인 |
| Locust 실행 환경 port-forward 불가 | 실환경 사용자 시뮬 불가 시 내부 k8s Job으로 대체 |
| 50명 부하 시 RDS bottleneck | RDS connection 수 모니터링 + Phase 1c RDS 업그레이드 백로그 |

## 10. 후속 단계

Phase 1b 완료 → **Phase 1c 설계 사이클**:
- CP-20/22 실체 + /auth/issue-jwt + T20 refresh daemon
- FileAuditAction + Skills governance + Admin Dashboard
- DEFAULT_USER_ROLE + Locust cookie + psql CI
- Phase 1a backlog 9건

Phase 1c 기간: 2주 예상

## 11. 참조

- Phase 1a 설계: `docs/superpowers/specs/2026-04-12-phase1a-security-hardening-design.md`
- Phase 1a plan: `docs/superpowers/plans/2026-04-12-phase1a-security-hardening.md`
- Phase 1a 합동 보고서: `docs/qa/phase1a-joint-report.md`
- Phase 0 TODOS #5: EKS 50명 상시 운용 사이징 재계산 (원본 요건)
