# Phase 1b — 50-user Locust SLO Baseline

**Date**: 2026-04-13 18:08:52 KST
**Test**: `tests/load/locustfile.py`
**Target**: https://claude.skons.net (auth-gateway)
**Parameters**: 50 users / spawn-rate 5 / 5 min headless
**Token**: TEST_USER_TOKEN (TESTUSER01, ALLOW_TEST_USERS=true)
**Locust version**: 2.43.4

## Tested Endpoints

| Endpoint | Weight | Remarks |
|----------|--------|---------|
| `GET /health` | 5 | No auth, baseline latency |
| `GET /api/v1/sessions/` | 4 | JWT required, DB read |
| `GET /api/v1/guides/` | 2 | JWT required, read-only |
| `GET /api/v1/skills/` | 1 | JWT required, read-only |

> **Note on locustfile update**: The original locustfile targeted Phase 0 Open WebUI
> endpoints (`/api/chat/completions`, `/api/models`). These endpoints do not exist on
> `claude.skons.net` (which routes to auth-gateway, not Open WebUI). The locustfile was
> corrected to target actual auth-gateway endpoints before Phase 1b execution.

## SLO Acceptance

| 항목 | SLO | 실측 | 판정 |
|------|-----|------|------|
| p95 (전체) | < 150 ms | **64 ms** | **PASS** |
| p99 (전체) | - | 100 ms | 참고 |
| max (전체) | - | 186 ms | 참고 |
| 에러율 | < 1% | **0.0%** | **PASS** |
| RPS (전체) | ~250 rps (Phase 1 목표) | 9.2 rps | - (50명 × wait 2-8s 기준 정상) |

### 엔드포인트별 결과

| Endpoint | p50 | p95 | p99 | max | 요청수 | 에러 | 판정 |
|----------|-----|-----|-----|-----|--------|------|------|
| GET /health | 10 ms | 22 ms | 38 ms | 82 ms | 1,173 | 0 | PASS |
| GET /api/v1/guides/ | 25 ms | 38 ms | 53 ms | 167 ms | 442 | 0 | PASS |
| GET /api/v1/skills/ | 25 ms | 43 ms | 75 ms | 168 ms | 239 | 0 | PASS |
| GET /api/v1/sessions/ | 41 ms | 88 ms | 130 ms | 186 ms | 901 | 0 | PASS |
| **Aggregated** | **24 ms** | **64 ms** | **100 ms** | **186 ms** | **2,755** | **0** | **PASS** |

## HPA Scale Events

50 users 부하 중 HPA가 관측한 CPU 사용률:

- 부하 이전: cpu 1-2% / 70%
- 부하 중 피크: **cpu 18% / 70%** (최대 관측치)
- 부하 중 평균: cpu 9-11% / 70%
- memory: 62% / 80% (안정 유지)
- **replicas: 2 유지 (scale-up 없음)**

HPA scale-up 이벤트 없음. 50 users 부하에서 CPU 최대 18% 수준으로 min replicas 2가 충분히 처리.

## Pod Events

부하 테스트 전 기간 동안 2개 Pod 안정 유지. 신규 Pod 생성 없음.

```
auth-gateway-799c887bcb-7vwd2   1/1 Running  (system-node-large A)
auth-gateway-799c887bcb-mfxcn   1/1 Running  (system-node-large B)
```

Anti-affinity hard rule 준수 — 각 Pod가 별도 노드에 배치됨.

## 최종 판정

**SLO PASS**

- p95 64ms — SLO 150ms 대비 **2.3배 여유**
- 에러율 0.0% — SLO 1% 대비 완전 충족
- HPA scale-up 없음 — 50 users는 min=2 replicas로 처리 가능
- PDB minAvailable=2 — 전 기간 유지

## Phase 1c 이관 관찰사항

1. **RPS 여유**: 9.2 RPS (50 users, wait 2-8s) → Phase 2 (팀장 50명 동시) 시나리오에서도 여유 있음
2. `/api/v1/sessions/` p95=88ms — DB 쿼리 포함, 연결 풀 모니터링 권장
3. CPU 최대 18%로 scale-up 미발생 — HPA cpu 임계값 70% 유효
4. memory 62%/80% 안정 → ElastiCache main_tls CPU 병행 모니터링 필요
5. **locustfile 경로 정합성 주의**: Phase 0 (Open WebUI `/api/chat/completions`)와 Phase 1b (auth-gateway `/api/v1/*`)는 타겟이 다름. 향후 테스트 시 --host 와 경로를 명시적으로 구분할 것
6. replicas 2 하드코딩된 부분 정리 필요 (Task 4 review note 이월)

## 환경 정보

| 항목 | 값 |
|------|-----|
| EKS Cluster | bedrock-claude-cluster (ap-northeast-2) |
| auth-gateway nodegroup | system-node-large (t3.large) × 2 |
| HPA | min=2 / max=4 / cpu 3%/70% / mem 60%/80% |
| PDB | minAvailable=2 |
| Ingress | ingress-workers (t3.large) × min 2 |
| Locust | 2.43.4 / Python 3.14.3 / macOS (ARM) |
| Test duration | 5m (18:03:52 ~ 18:08:52 KST) |
