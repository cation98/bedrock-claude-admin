# 법무/정보보안팀 질의서 — OnlyOffice AI 플러그인 사내 통합

**작성일**: 2026-04-12
**작성자**: 사내 Bedrock AI 플랫폼 담당 (cation98)
**관련 문서**:
- 설계: `docs/plans/2026-04-12-onlyoffice-ai-integration-design.md`
- 기존 승인 이력: TODOS.md #15 (OnlyOffice AGPL 법무팀 검토), #16 (Phase 5 Bedrock logging)

## 1. 배경 요약

현 bedrock-ai-agent 플랫폼은 SK 임원·팀장·실무자용 내부 Claude Code 활용 플랫폼이다. 사용자는 격리된 K8s Pod에서 Claude Code 터미널을 사용하며, AWS Bedrock(Claude Sonnet 4.6 / Haiku 4.5)을 VPC endpoint로 호출 중이다. **이미 승인된 운영 경로**다.

이번 안건은 기존 OnlyOffice Document Server(문서 뷰어/에디터)에 AI 기능을 추가하는 건이다. 구체적으로 사용자가 Excel/Word/PPTX 문서에서 **텍스트를 선택하고 AI 메뉴(요약/번역/교정/보고서초안)를 호출**하면 해당 텍스트가 내부 auth-gateway를 통해 Bedrock으로 전송되어 응답을 받아 문서에 삽입되는 기능이다.

## 2. 2개 독립 쟁점

두 별개 법무 이슈가 섞여 있으므로 분리하여 답변을 요청한다.

### 쟁점 A — OnlyOffice Community Edition AGPL-3.0 컴플라이언스

**사실관계**:
- 현재 배포: `onlyoffice/documentserver:8.2.2` Community Edition (AGPL-3.0)
- 배포 위치: EKS 클러스터 내부, 사내 임직원 접근 전용
- **수정 범위**: OnlyOffice Document Server 이미지/core 코드는 **변경하지 않음**. `sdkjs-plugins/` 디렉토리에 **자체 작성한** 신규 플러그인 drop-in만 수행 (공식 AI 플러그인 fork 없음)
- 플러그인 소스: SK 자체 저작, JavaScript ~500 LOC, OnlyOffice가 공개한 plugin SDK(`window.Asc.plugin`) API만 호출 (ABI-level use)

**질의사항 (A)**:

> Q-A1. OnlyOffice Document Server Community Edition을 **이미지 수정 없이 실행**하고, `sdkjs-plugins/` 디렉토리에 **자체 저작 플러그인 파일**을 볼륨마운트/ConfigMap 방식으로 추가하는 행위가 AGPL-3.0의 "modification"에 해당하여 당사 저작물(플러그인 소스) 공개 의무를 발생시키는가?
>
> Q-A2. `plugin-list-default.json` 파일은 OnlyOffice 이미지에 번들되어 있어, 이 파일을 ConfigMap으로 override하여 자체 플러그인 UUID를 추가하는 것이 AGPL의 "derivative work" 로 해석될 가능성이 있는가? 영향이 있다면 대안(init-container로 원본 유지 + merge)은 적절한가?
>
> Q-A3. 본 플랫폼은 **사내 임직원 전용**(외부 고객 접근 없음). AGPL의 network service 조항(Section 13)이 적용되는 범위에 사내 전용 배포가 포함되는지. 외부 공개 의무가 발생한다면 "요청 시 소스 제공" 으로 충족 가능한가?

### 쟁점 B — 데이터 거버넌스 (Bedrock으로 문서 콘텐츠 송신)

**사실관계**:
- AI 호출 시 송신 데이터: 사용자가 명시적으로 선택한 텍스트 영역 (전체 문서 자동 업로드 아님)
- 송신 경로: 사용자 브라우저 → OnlyOffice 플러그인 → auth-gateway (내부) → AWS Bedrock VPC endpoint → Claude API
- **Bedrock은 기존 Claude Code Pod에서 이미 사용 중** — 동일 region, 동일 모델, 동일 IRSA 패턴
- AWS 공식 정책: "Bedrock은 고객 입력을 Foundation Model 학습에 사용하지 않음" (AWS Bedrock Security FAQ 2024)
- 본 서비스 audit log에 모든 AI 호출 기록 (prompt 해시, 응답 길이, 사용자 ID, 모델, 토큰 수)

**질의사항 (B)**:

> Q-B1. 기존 Claude Code Pod의 Bedrock 사용이 법무/정보보안팀 승인을 받은 것으로 이해한다. OnlyOffice AI 플러그인의 Bedrock 사용이 **같은 승인 범위 내**로 해석 가능한가, 아니면 **별도 승인이 필요한 확장**인가?
>
> Q-B2. 문서 내용이 프롬프트에 포함되어 송신될 때, 어떤 데이터 분류 등급까지 허용 가능한가? ("기밀"/"대외비"/"일반" 등 사내 분류 체계에 맞춰 답변 요청)
>
> Q-B3. DLP(Data Loss Prevention) 정책 적용 시점 — 프롬프트 **전송 전 스캔** vs **응답 후 기록 스캔** 중 어느 것이 표준인가? 두 방식의 권장 조합은?
>
> Q-B4. CloudWatch 및 auth-gateway audit_log에 기록되는 prompt 해시/응답 길이/사용자 ID의 **보존 기간**과 **접근 권한 분리 요건**은?
>
> Q-B5. 사용자에게 "문서 내용이 Bedrock으로 전송됨" 을 고지하는 UI 문구/동의 프로세스가 필요한가? 필요하다면 one-time consent 로 충분한가, 매 호출마다 표시 의무가 있는가?

## 3. 당사 제안 (법무팀 검토 대상)

- AGPL: 자체 플러그인 작성 경로로 공개 의무 회피. 공식 OO AI 플러그인 fork/수정 금지 정책 수립.
- 데이터 분류: 1차 배포는 **"일반" 및 "대외비" 등급** 문서에 한정. "기밀" 등급은 UI에서 AI 메뉴 숨김 처리.
- DLP: **프롬프트 전송 전 스캔** (file_governance 서비스 재사용). 위반 시 400 응답 + 사용자 알림.
- 동의: 플러그인 설치 후 **최초 1회** consent 모달 → 이후 사용자별 localStorage에 승인 기록.
- 로그 보존: 180일 (기존 감사 정책 준용), 접근 권한은 감사팀/보안팀 분리.

## 4. 일정 및 연락

- 질의 송부: 2026-04-12
- 회신 요청: **PoC 완료 전(Week 1) 또는 Pilot 시작 전(Week 4) 이내**. PoC 내부 시연은 본 건과 무관하게 진행 가능(실데이터 미사용).
- 담당자: cation98 (N1102359)

법무팀/정보보안팀 담당자의 서면 회신을 요청드립니다. 필요 시 질의 단축 회의(30분) 조율 가능합니다.

---

**첨부** (원하시면 추가 제공):
- OnlyOffice AGPL-3.0 원문 링크
- AWS Bedrock Security & Privacy FAQ 발췌
- 기존 Claude Code Pod 승인 문서 번호
- 플러그인 소스 미리보기 (`container-image/onlyoffice-plugins/sk-ai/`)
