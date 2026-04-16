# PRD: 실시간 협업 플랫폼 확장 (4트랙)

**작성일**: 2026-04-15
**목표 구현 시점**: 2026-04 중 (약 2주 내)
**상태**: 설계 예정 / 미착수
**Owner**: TBD
**관련 세션**: mindbase `d738b803-0a7b-49f4-87b1-4addd46bf368`

---

## 1. 배경 및 목표

현재 플랫폼은 **개인 격리 모델**로 설계되어 있음:
- OnlyOffice: per-Pod fileserver, document_key per-session rotation
- 텔레그램 봇(`t.me/sko_claude_bot`): 사용자별 독립 응답
- 배포 웹앱(Streamlit/Chainlit): per-session 격리
- AI 상호작용: 1인 단독

**목표**: 여러 명이 동일 문서/세션/대화에서 **실시간으로 응답을 공유하고 함께 편집**하도록 확장. AI도 "가상 협업자"로 참여.

---

## 2. 4개 트랙 요구사항

### Track 1 — OnlyOffice 동시편집

**현재 차단 요인** (memory: `debug-onlyoffice-key-rotation`):
- 파일이 각자 Pod fileserver에 고립 (`/personal/{username}/...`)
- document_key 세션별 rotation + salt
- edit_sessions 1인 ephemeral state

**요구사항**:
- 공유 스토리지(S3 / EFS 공용 경로)로 파일 이관
- 동일 파일 경로 → 동일 document_key 매핑 (salt 제거)
- 권한 모델 (owner, editor 초대, viewer, ACL)
- 저장 콜백 race 처리 (status=2 version 경쟁)
- Community Edition 20명 동시 한도 검증

### Track 2 — 텔레그램 챗봇 공유 응답

**요구사항**: 한 사용자의 질의/응답을 동일 그룹/세션 참여자에게 실시간 브로드캐스트.

- 공유 세션 모델 (group chat / 초대 토큰)
- 메시지 fan-out 경로 (Redis Pub-Sub 또는 Stream — 기존 usage_events 재활용 검토)
- 참여자 권한 (read-only / reply-allowed / admin)
- 텔레그램 Bot API rate limit (30 msg/sec per bot) 대응

### Track 3 — 배포 웹앱 실시간 협업

**대상**: DeployedApp (Streamlit / Chainlit / custom).

- WebSocket / SSE fan-out
- BedrockAccessGateway `usage_events` Stream 재활용 검토
- 세션 격리 단위 (per-app vs per-room)
- ingress-nginx WebSocket 확장성 검증 (현 t3.large × 2~6 노드)
- Open WebUI의 기존 WebSocket 패턴 참조

### Track 4 — OnlyOffice AI 공동편집 (Track 1 확장)

**전제**: Track 1 완료 후 확장. 공유 document_key + 공유 스토리지 필수.

**OnlyOffice AI 플러그인 활성화**:
- OnlyOffice DS 8.1+ 기본 내장(비활성) → config 주입
- `baseUrl` → `http://bedrock-access-gateway:8080/v1` (IRSA로 실제 인증)
- `apiKey` → dummy (실제 경로는 gateway가 처리)

**AI를 가상 편집자로 모델링**:
- 사람 N명 + AI 1명이 동일 document_key 접속
- AI 응답 → OT(Operational Transform) 연산으로 주입
- 스트리밍 응답 청크 단위 문서 삽입 (타이핑 효과 + 다른 편집자에게 실시간 표시)
- AI 편집은 별도 track changes로 표시 → 인간 승인 후 확정

**보안 / Governance**:
- 프롬프트 주입 방지 (`ai-content-moderation-pattern` Haiku 사전 필터 재사용)
- per-user 한도 (`token_quota_assignments`) AI 호출에도 적용
- AI 편집 이력 audit 테이블 (Phase 2 A+B Skills governance 감사테이블 확장)
- "AI 편집 권한" flag 도입 검토 (`User.can_use_ai_collab`)

---

## 3. 공통 기반 레이어 (중복 구현 방지)

4개 트랙 공통 요구사항 → 하나의 레이어로 설계:

| 레이어 | 역할 | 재사용 대상 |
|--------|------|-------------|
| 공유 스토리지 | S3 / EFS 공용 경로, 버전 관리 | Track 1, 4 |
| 권한 모델 | room / invite / ACL, 참여자 role | Track 1~4 |
| 실시간 전달 | Redis Pub-Sub / Stream fan-out | Track 2, 3, 4 |
| Presence / Cursor | 누가 지금 보는지 + 커서/선택 영역 | Track 1, 3, 4 |
| Audit / Governance | 누가 언제 무엇을 변경/발화 (Phase 1 SoD 연계) | Track 1~4 |

---

## 4. 선행 조건 및 의존성

- **Phase 2 C** (Admin Dashboard UI) 완료 후 착수 권장
- **Phase 2 E** (N-of-M 승인) 완료 후 Governance 레이어 설계
- 보안 검토: 공유 세션에서 타 사용자 데이터 누출 차단 (per-user IRSA 모델 정합성)
- 비용 검토: fan-out 시 Bedrock 토큰 중복 과금 정책
- 인프라 검증: ingress-workers nodegroup WebSocket 부하 테스트

---

## 5. 구현 순서 (제안)

1. **Week 1** (2026-04-15 ~ 04-21)
   - 공통 기반 레이어 설계 문서 확정 (PoC 스파이크 포함)
   - Track 1 (OnlyOffice 동시편집) 구현 착수
2. **Week 2** (2026-04-22 ~ 04-28)
   - Track 1 완료 + Track 4 (AI 공동편집) 설계 확정
   - Track 2 (텔레그램) / Track 3 (웹앱) 병렬 설계
3. **Week 3** (2026-04-29 ~)
   - Track 2~4 구현 + 통합 테스트

> 실제 일정은 Phase 2 C/E 진척도에 따라 재조정.

---

## 6. 성공 기준

- Track 1: 동일 문서에 3명 이상 동시 편집, 저장 충돌 없이 반영
- Track 2: 그룹 세션에서 한 명 응답이 1초 내 타 참가자에게 전파
- Track 3: 웹앱 1개 세션에 5명 접속, 응답 스트리밍 동기 확인
- Track 4: AI 제안이 모든 편집자에게 실시간 표시 + track changes 승인 워크플로 동작

---

## 7. 관련 문서 및 메모리

**문서**:
- `docs/plans/2026-04-12-onlyoffice-ai-integration-design.md` (기 설계)
- `docs/plans/2026-04-12-onlyoffice-ai-integration-test-plan.md`
- `docs/plans/2026-03-21-bedrock-claude-code-platform-design.md` (전체 아키텍처)

**mindbase 메모리**:
- `future-realtime-collab-design-todo` — 본 PRD 요약 TODO
- `debug-onlyoffice-key-rotation` — document_key 재사용 금지 교훈
- `checkpoint-onlyoffice-edit-mode-design` — 편집 모드 기존 설계
- `infra-system-nodegroup-separated` — ingress-workers WebSocket 확장성 기반
- `bedrock-security-architecture-design` — per-user 격리 정합성 검토
- `ai-content-moderation-pattern` — AI 프롬프트 필터 재사용 패턴
- `phase1-backlog-cleanup-20260414` — Phase 1/2 진척 컨텍스트
