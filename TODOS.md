# TODOS

## Infra: 50명 상시 운용 기준 EKS 사이징 재계산

**Priority**: High
**Added**: 2026-04-09 (from /plan-eng-review Codex outside voice)

현재 4 x m5.xlarge (64Gi total)로 50 Pod (75Gi 요청)을 감당 못함.
max_pods_per_node=1, pod memory_request=1.5Gi 기준.

재계산 필요:
- 노드 수: 최소 50개 (1-pod-per-node) 또는 노드당 다중 Pod 허용 시 재설계
- 인스턴스 타입: t3.medium(4Gi) → t3.large(8Gi) 또는 m5.large 검토
- OnlyOffice + Redis + Squid proxy 추가 리소스 반영
- EFS throughput mode 확인 (50명 동시 I/O)

**Depends on**: 보안 구현(Phase 1-4)과 병행 가능
**Context**: CLAUDE.md의 Developer Context에서 Phase 1 MVP → Phase 2 (팀장 50명) → 상시 운영 경로가 "상시 운영 50명, 전사 운영 목표"로 변경됨.

## Security: TEST* 사용자 SSO/2FA 바이패스 제거

**Priority**: Critical
**Added**: 2026-04-09 (from Codex outside voice)

auth.py에 TEST* 사용자가 SSO+2FA를 건너뛸 수 있는 바이패스 존재.
퍼블릭 인터넷에서 이것은 보안 구멍. Phase 1 Critical 수정에 포함해야 함.

## Security: 기존 file_share ACL → 새 거버넌스 브로커 전환 전략

**Priority**: Medium
**Added**: 2026-04-09 (from Codex outside voice)

새 파일 거버넌스 브로커가 기존 shared_datasets/file_share_acl을 어떻게 대체하는지 미정의.
이중 메타데이터, 이중 정책 평가 위험. Phase 2에서 명확한 전환 계획 필요.
