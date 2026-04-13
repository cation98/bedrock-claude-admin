# Phase 1b 합동 검증 보고 — 50명 상시 운용 Scale 대응

**Date**: 2026-04-13 (작업 세션)
**Branch**: `feat/phase1b-scale-50users`
**Base**: main HEAD `cd25c01` (Phase 1a merged + Phase 1b spec)
**HEAD**: Task 6 commit (이 보고서 포함)
**Commits**: 8 (Task 1~5 + fix 2건 + Task 6)

## 요약

Phase 1b Task 1~5 구현 + 검증 완료. 50명 상시 운용을 위한 EKS 스케일 + ElastiCache 정리 완료.
합동 보고서 최종 게이트 10/10 충족. **merge 상태: 사용자 승인 대기 중.**

---

## Section 1 — ElastiCache standalone destroy

**목표**: standalone `bedrock-claude-redis` 제거, `main_tls` replication group 단독 운영.

| 항목 | 결과 |
|------|------|
| AWS standalone clusters (no ReplicationGroupId) | `safety-sko-*` 2개 (별 프로젝트, 무관) |
| `bedrock-claude-redis` standalone | 미존재 (destroy 완료) |
| `bedrock-claude-redis-tls` replication group | 운영 중 (primary endpoint 사용) |
| ExternalName Service `elasticache-redis` (openwebui ns) | `main_tls` primary로 cutover 완료 |
| auth-gateway Redis PING | `True` (rediss://, TLS 무중단) |

**live 확인** (Security spot-check):
- URL scheme: `rediss`
- host: `master.bedrock-claude-redis-tls.khpawo.apn2.cache.amazonaws.com`
- PING: `True`

상세 커밋: `3b03fa4` (standalone destroy) + `5998956` (ExternalName cutover)

---

## Section 2 — EKS nodegroup sizing

**목표**: 50명 동시 세션 대응 워커 용량 확보.

| Nodegroup | desired (Terraform) | desired (live) | min | max | instance | capacity |
|-----------|---------------------|----------------|-----|-----|----------|---------|
| `bedrock-claude-nodes` (main) | **6** | 4 (CA 축소) | 0 | **12** | m5.large | ON_DEMAND |
| `bedrock-claude-burst-workers` (신규) | 0 | 0 | 0 | 4 | m5.xlarge / m5a.xlarge | SPOT |

**주의**: main nodegroup live desired=4는 Cluster Autoscaler가 로드테스트 후 축소한 결과.
`lifecycle { ignore_changes = [desired_size] }`는 burst_workers에만 적용됨 — CA 운영 정상.
Terraform tfvars `eks_node_desired_size=6, eks_node_max_size=12` 확정.

상세 커밋: `9763556` (main 6/12) + `0a73701` (burst-workers SPOT)

---

## Section 3 — auth-gateway HPA + PDB + Deployment Strategy

**목표**: 50명 트래픽 급증 대응 자동 스케일 + 무중단 배포 보장.

| 항목 | 이전 | 이후 |
|------|------|------|
| HPA minReplicas | 1 | **2** |
| HPA maxReplicas | 3 | **4** |
| HPA target CPU | 80% | **70%** |
| HPA target Memory | — | **80%** |
| HPA scaleUp stabilization | 60s | **30s** |
| HPA scaleDown stabilization | 300s | 300s (유지) |
| PDB minAvailable | — | **2** |
| Deployment maxSurge | 1 | 1 (유지) |
| Deployment maxUnavailable | 1 | **0** |
| requests.memory | 256Mi | **320Mi** |

**live 확인**:
```
NAME           REFERENCE                 TARGETS                        MINPODS   MAXPODS   REPLICAS   AGE
auth-gateway   Deployment/auth-gateway   cpu: 0%/70%, memory: 62%/80%   2         4         2          32m

NAME           MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
auth-gateway   2               N/A               0                     32m
```

상세 커밋: `34f5c9b` (HPA 2/4 + PDB 1) + `4cf41cc` (memory 320Mi + PDB 2)

---

## Section 4 — Locust 50-user SLO 결과

**시나리오**: auth-gateway 실 경로 50 VU 10분 부하 (GET /health, /api/v1/sessions, /guides, /skills)

| 항목 | SLO | 실측 | 판정 |
|------|-----|------|------|
| p95 (전체) | < 150 ms | **64 ms** | **PASS** (2.3× 여유) |
| 에러율 | < 1% | **0.0%** (2755 req / 0 fail) | **PASS** |
| HPA scale-up | 기대 관찰 | 미발동 (CPU 18%) | OBSERVED — 단순 GET 부하 |

**엔드포인트별 p95**:
- `/health` 22ms / `/guides` 38ms / `/skills` 43ms / `/api/v1/sessions` 88ms

**시나리오 정정 사항**: 초기 locustfile이 Phase 0 Open WebUI 경로(`/api/chat/completions`)로
작성됐으나 claude.skons.net은 auth-gateway routing → 404 발생. auth-gateway 실 경로로 정정.

**한계**: end-to-end LLM 부하(Pipelines + BAG + Bedrock)는 미측정 → Phase 1c 별도 시나리오 권고.

상세: `docs/qa/phase1b-50user-baseline.md` + 커밋 `0a81e9e`

---

## Section 5 — Security spot-check (Task 6 재검증)

| 항목 | 확인 내용 | 결과 |
|------|----------|------|
| rediss transit (main_tls 단독) | URL scheme=rediss, PING True | **PASS** |
| KMS rotation 유지 | KeyRotationEnabled=true, 365일 | **PASS** |

KMS Key ID: `bc47d786-64b9-42ae-8d03-58374253dd23`

---

## Section 6 — 회귀 테스트 결과 (Task 6 재실행)

```
92 passed, 2 skipped, 30 warnings
```

**수정 사항**: `test_k8s_service.py::TestWriteLocalFileToPod::test_uses_kubernetes_stream_not_kubectl_subprocess`
- 증상: 전체 suite 실행 시 1 fail (단독 실행 시 pass)
- 원인: `asyncio.get_event_loop().run_until_complete()` — Python 3.12에서 다른 async test 후
  event loop 닫힘, `except Exception` 으로 silently 삼킴 → `captured` 비어 assertion fail
- 수정: `asyncio.run()` 으로 교체 (항상 새 event loop 생성, Python 3.12 권장 패턴)
- 결과: 92 passed (전 Phase 1a baseline 91 → +1 확정)

---

## Section 7 — Phase 1c 이관 백로그

### 신규 관찰 사항 (Phase 1b에서 발견)

| 항목 | 우선순위 | 설명 |
|------|---------|------|
| end-to-end LLM 부하 시나리오 | HIGH | Pipelines + BAG + Bedrock 50명 동시 chat |
| RDS connection pool 모니터링 | MEDIUM | sessions 88ms baseline 기준 연결 수 추이 |
| ElastiCache main_tls CPU utilization | MEDIUM | 50명 동시 세션 캐시 부하 확인 |
| Open WebUI pipelines HPA 도입 | LOW | auth-gateway와 동일 패턴 적용 검토 |

### 기술 부채

| 항목 | 설명 |
|------|------|
| `Deployment.spec.replicas=2` hardcoded | HPA owner 보장 위해 제거 필요 (GitOps argocd/kustomize 도입 시점) |
| main nodegroup `min_size=0` cold-start | 야간 축소 후 첫 접속 latency 급등 — 운용 절차 문서화 또는 `min=2` 상향 검토 |
| locustfile end-to-end 시나리오 누락 | Open WebUI chat 부하 시나리오 별도 클래스 추가 |
| `variables.tf eks_node_max_size` description | "Phase 2" 참조가 남아있음 — "Phase 1b" 으로 업데이트 필요 |

### Phase 1a 이관 백로그 (재확인)

| ID | 항목 |
|----|------|
| CP-20 | budget_gate 실체 (50명 비용 폭주 방지) |
| T20 | background token refresh daemon (15분 TTL) |

---

## 완료 게이트 (spec §8) — 10/10 충족

| # | 항목 | 결과 |
|---|------|------|
| 1 | standalone cluster 미존재 (`bedrock-claude-redis`) | ✅ PASS |
| 2 | main nodegroup max 12, Terraform desired 6 설정 | ✅ PASS (live=4, CA 정상 축소) |
| 3 | burst-workers desired 0 (idle 대기) | ✅ PASS |
| 4 | HPA min 2 / max 4 active | ✅ PASS |
| 5 | Locust 50 users p95 < 150ms PASS | ✅ PASS (64ms) |
| 6 | Locust 에러율 < 1% PASS | ✅ PASS (0.0%) |
| 7 | 회귀 테스트 92 passed (Phase 1a baseline 유지 + 1) | ✅ PASS |
| 8 | 합동 보고서 작성 | ✅ PASS |
| 9 | security spot-check 2건 (rediss + KMS) PASS | ✅ PASS |
| 10 | main merge 준비 (실행은 사용자 승인 후) | ✅ 대기 중 |

**결과: 10/10 게이트 충족 → Phase 1b 완료 (merge 승인 대기)**

---

## main merge 준비 명령어 (실행 금지 — 사용자 승인 후 별도 세션)

```bash
# 사용자 승인 후 아래 순서로 실행:
cd /Users/cation98/Project/bedrock-ai-agent

git checkout main
git merge --no-ff feat/phase1b-scale-50users -m "$(cat <<'EOF'
Merge Phase 1b: 50명 상시 운용 scale 대응

- standalone destroy + ExternalName main_tls cutover
- main nodegroup desired 6 / max 12 (m5.large, ON_DEMAND)
- burst-workers SPOT nodegroup 신규 (m5.xlarge/m5a.xlarge, max 4)
- auth-gateway HPA min 2 / max 4 + memory 320Mi + PDB 2 + strategy maxSurge=1
- Locust 50-user p95=64ms / err=0% PASS (SLO: 150ms / 1%)
- security spot-check (rediss + KMS rotation) PASS
- 회귀 92 passed (test_k8s_service asyncio.run() 수정 포함)

end-to-end LLM 부하 + RDS pool 모니터링 → Phase 1c 이관.
상세: docs/qa/phase1b-joint-report.md
EOF
)"
```

**이 세션에서 merge 실행 금지. 사용자 명시 승인 후 별도 세션에서 실행.**
