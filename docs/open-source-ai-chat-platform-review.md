# Open Source AI Chat Platform Review

> Bedrock 백엔드 기반 사내용 AI 채팅 플랫폼 오픈소스 검토  
> 검토일: 2026-04-11 ~ 2026-04-12

---

## 1. 검토 배경 및 목표

AWS Bedrock (Claude 모델)을 백엔드로 사용하여 사내용 AI 채팅 환경을 구축하기 위한 오픈소스 플랫폼 검토. 다음 요건을 충족하는 솔루션을 탐색함:

- **Bedrock 백엔드 연결** (Claude Sonnet/Opus)
- **로컬 파일 접근** (MCP 프로토콜)
- **스킬 시스템** (Claude Code의 CLAUDE.md + Skills 패러다임)
- **멀티 유저** (2,000명+ 대기업 규모)
- **라이선스** (상용 사용 가능, 비용 최소화)
- **리치 콘텐츠 렌더링** (차트, Mermaid, HTML, Artifacts)
- **문서 협업 연동** (OnlyOffice)

---

## 2. 후보 플랫폼 비교

### 2.1 주요 후보 요약

| 플랫폼 | 형태 | 라이선스 | Bedrock | MCP | 스킬 시스템 | 2,000명 규모 |
|--------|------|---------|---------|-----|-----------|-------------|
| **Open WebUI** | 웹 (브라우저) | BSD-3 | O (BAG/LiteLLM) | O (v0.6.31+) | **네이티브** | 무료 |
| **LibreChat** | 웹 (브라우저) | MIT | O (네이티브) | O | X (Agent로 대체) | 무료 |
| **Cherry Studio** | 데스크톱 앱 | AGPL-3.0 | O (v1.5.4+) | O | X | 10명 초과 시 상용 라이선스 |
| **LobeChat** | 웹/데스크톱 | MIT | O | O | 플러그인 방식 | 무료 |
| **AnythingLLM** | 데스크톱 앱 | MIT | X (LiteLLM 필요) | O (v1.8.0+) | X | 무료 |
| **Dify** | 웹 (워크플로우) | 제한적 | O | 제한적 | 워크플로우 방식 | 라이선스 확인 필요 |

### 2.2 최종 권장: Open WebUI

**Open WebUI가 모든 요건에 가장 근접하며, 스킬 시스템이 네이티브로 내장됨.**

---

## 3. Open WebUI 상세 검토

### 3.1 스킬 시스템 (핵심 차별점)

Open WebUI는 Claude Code의 Skill과 거의 동일한 네이티브 스킬 시스템을 보유:

```
Claude Code                          Open WebUI
─────────────────                    ─────────────────
.claude/skills/skill.md       ≈      Skills (UI/API로 관리)
/skill-name 입력               ≈      채팅바에서 스킬 선택
<available_skills> 태그         ≈      <available_skills> 태그
view_skill 도구                ≈      view_skill 도구
개인 스킬 생성                  ≈      개인 스킬 생성/관리
글로벌 스킬 공유               ≈      공유 스킬 (전체/그룹)
```

**동작 원리:**
1. 스킬이 모델에 연결되면 → `<available_skills>` 태그로 이름+설명이 시스템 프롬프트에 주입
2. `view_skill` 도구가 자동 주입 → AI가 필요 시 전체 지침 동적 로드
3. 사용자가 채팅바에서 직접 스킬 선택 시 → 전체 내용이 시스템 프롬프트에 직접 주입

**Skills API:**
```
GET  /api/skills/list?view_option=all        # 전체 스킬
GET  /api/skills/list?view_option=personal   # 내 스킬
GET  /api/skills/list?view_option=shared     # 공유된 스킬
```

**참고:** Anthropic/OpenAI SKILL.md 포맷 직접 지원 Feature Request 진행 중 (Issue #19941)

### 3.2 Bedrock 연결 방법

LiteLLM은 필수가 아님. 3가지 경로 존재:

| 방법 | 설명 | 별도 서버 | 권장 규모 |
|------|------|----------|----------|
| **Bedrock Access Gateway** | AWS 공식 오픈소스, OpenAI 호환 API 변환 | O (경량) | PoC ~ 500명 |
| **LiteLLM Proxy** | 멀티 프로바이더 게이트웨이, 비용 추적 | O | 500명+ / 멀티모델 |
| **Bedrock Mantle** | AWS 네이티브, 설치 불필요 | **X** | 최소 운영 |

**2,000명 규모 + Bedrock 단일 사용 시 Bedrock Access Gateway 권장.**

```yaml
# docker-compose.yml (권장 구성)
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    ports:
      - "3000:8080"
    environment:
      - OPENAI_API_BASE_URL=http://bedrock-gateway:8000/api/v1
      - OPENAI_API_KEY=dummy

  bedrock-gateway:
    image: aws-samples/bedrock-access-gateway
    environment:
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
      - AWS_DEFAULT_REGION=us-east-1
```

### 3.3 리치 콘텐츠 렌더링

| 유형 | 지원 | 비고 |
|------|------|------|
| Mermaid 다이어그램 | O | 인라인 자동 렌더링 |
| Chart.js | O | Artifacts 패널에서 인터랙티브 |
| D3.js | O | 데이터 시각화 |
| Three.js | O | 3D 시각화 |
| HTML/CSS/JS | O | Artifacts 패널 |
| SVG | O | 인라인 + Artifacts |
| LaTeX 수식 | O | 인라인 + 블록 |
| Vega/Vega-Lite | O | 선언적 데이터 시각화 |
| Python 코드 실행 | O | gVisor 샌드박스 |
| Rich UI 임베딩 | O | 도구/액션이 HTML을 채팅에 직접 삽입 |

### 3.4 MCP 지원

- v0.6.31부터 네이티브 MCP 지원
- HTTP/SSE 엔드포인트 직접 연결
- 연결 중복 제거 및 복원력 내장

### 3.5 엔터프라이즈 기능

| 기능 | 지원 |
|------|------|
| LDAP / SSO (SAML, OIDC) | O |
| RBAC (역할 기반 접근 제어) | O |
| 멀티유저 격리 | O |
| Admin Dashboard | O |
| 사용량 추적 | O |
| Rate Limiting | O |

### 3.6 라이선스

- **BSD-3-Clause** — 상용 사용 무제한, 수정/배포 자유, 소스 공개 의무 없음
- 2,000명+ 규모에서도 라이선스 비용 **$0**

---

## 4. LibreChat 검토 결과

### 4.1 장점
- MIT 라이선스 (완전 자유)
- Bedrock **네이티브** 지원 (게이트웨이 불필요)
- MCP 네이티브 지원
- Artifacts (HTML, React, Mermaid, SVG)
- LDAP/SAML/OIDC SSO
- Daimler Truck, Shopify 등 대기업 도입 실적

### 4.2 한계
- **스킬 시스템 미내장** — Agent 기능으로 대체해야 하며, Claude Code 스킬 패러다임 재현 어려움
- Agent + Bedrock + MCP 조합에서 호환성 이슈 존재 (Issue #8482)
- 스킬 시스템 구현 시 커스텀 MCP 서버 개발 필요 (1-2주)

### 4.3 LibreChat이 적합한 경우
- 스킬 시스템 불필요, 기본 채팅 + Artifacts만 필요한 경우
- Bedrock 직접 연결이 중요한 경우 (게이트웨이 없이)
- Agent 기반 워크플로우로 충분한 경우

---

## 5. Cherry Studio 검토 결과

### 5.1 장점
- **데스크톱 앱** — 로컬 파일에 MCP로 직접 접근 가능
- Bedrock 네이티브 지원 (v1.5.4+)
- MCP 지원
- 300+ 어시스턴트, RAG, 멀티모델

### 5.2 한계
- **AGPL-3.0 + 상용 듀얼 라이선스**: 10명 초과 조직은 상용 라이선스 필수
- 2,000명 규모 시 상용 라이선스 비용 발생 (bd@cherry-ai.com 문의)
- 웹 기반이 아닌 데스크톱 앱 → 각 PC에 설치 필요

### 5.3 기술 스택
- Electron + TypeScript + React 19
- electron-vite 빌드
- Express 5.1.0 (로컬 API 서버)
- Drizzle ORM + LibSQL

### 5.4 Cherry Studio가 적합한 경우
- 로컬 파일 직접 접근이 핵심 요건인 경우
- 소규모 팀 (10명 이하)
- 데스크톱 앱 형태가 선호되는 경우

---

## 6. OnlyOffice 연동

### 6.1 연동 가능성
- OnlyOffice DocSpace가 **공식 MCP 서버** 제공 (Apache-2.0)
- Open WebUI/LibreChat 모두 MCP 클라이언트이므로 직접 연결 가능

### 6.2 MCP 서버 제공 도구
```
create_room, upload_file, download_file_as_text, update_file,
create_folder, delete_folder, get_folder_content, set_room_security,
get_all_people, copy_batch_items, move_batch_items 등 22개 도구
```

### 6.3 AI + 문서 공동편집 시나리오
- AI가 문서 생성 → OnlyOffice 룸에 업로드 → 사용자가 공동 편집
- AI가 기존 문서 읽기 → 분석/요약/수정안 제안
- OnlyOffice 내장 AI 플러그인으로 편집 중 인라인 AI 지원 (Anthropic 직접 연결 가능)

### 6.4 라이선스
| 제품 | 라이선스 | 2,000명 사용 |
|------|---------|-------------|
| OnlyOffice DocSpace Community | Apache-2.0 | 무료 |
| OnlyOffice Docs Server | AGPL-3.0 | 무료 (사내 전용 시 배포 아님) |
| OnlyOffice DocSpace MCP Server | Apache-2.0 | 무료 |

---

## 7. 사내 배포 아키텍처 (권장)

### 7.1 전체 구성

```
┌──────────────────────────────────────────────────────────┐
│  사내 서버 (Docker Compose / ECS)                          │
│                                                          │
│  ┌────────────┐     ┌──────────────┐     ┌────────────┐ │
│  │ Open WebUI │────→│ Bedrock      │────→│ AWS        │ │
│  │ (AI 채팅)   │     │ Access       │     │ Bedrock    │ │
│  │            │     │ Gateway      │     │ (Claude)   │ │
│  │ Skills ✅   │     └──────────────┘     └────────────┘ │
│  │ MCP ✅      │                                         │
│  │ Artifacts ✅│     ┌──────────────────────────┐        │
│  │ RBAC ✅     │────→│ OnlyOffice DocSpace      │        │
│  │            │ MCP │ + MCP Server             │        │
│  └────────────┘     │ (문서 협업)               │        │
│       ↑              └──────────────────────────┘        │
│       │ LDAP/SSO                                         │
│  ┌────────────┐                                          │
│  │ Active     │                                          │
│  │ Directory  │                                          │
│  └────────────┘                                          │
└──────────────────────────────────────────────────────────┘
         ↑
    브라우저 접속 (2,000명+ 사용자)
```

### 7.2 사용자 유형별 전략

| 사용자 유형 | 도구 | 파일 접근 | 비용 |
|------------|------|----------|------|
| 일반 사용자 (1,800명) | Open WebUI (웹) | 업로드 / 공유 스토리지 MCP | $0 (라이선스) |
| 개발자/파워유저 (200명) | Claude Code / Claude Desktop | 로컬 MCP 직접 접근 | API 과금만 |

### 7.3 스킬 운용 구조

```
/skills/
├─ global/                    ← 관리자만 수정
│   ├─ 문서작성가이드
│   ├─ 코드리뷰
│   └─ 번역도우미
├─ teams/                     ← 팀별 (LDAP 그룹 매핑)
│   ├─ engineering/
│   └─ marketing/
└─ users/                     ← 개인 스킬
    ├─ hong@company.com/
    └─ kim@company.com/
```

---

## 8. 라이선스 비용 요약

| 구성 요소 | 라이선스 | 2,000명 비용 |
|-----------|---------|-------------|
| Open WebUI | BSD-3 | $0 |
| Bedrock Access Gateway | Apache-2.0 | $0 |
| OnlyOffice DocSpace Community | Apache-2.0 | $0 |
| OnlyOffice Docs Server | AGPL-3.0 | $0 |
| OnlyOffice MCP Server | Apache-2.0 | $0 |
| LiteLLM (선택) | MIT | $0 |
| **소프트웨어 라이선스 합계** | | **$0** |
| AWS Bedrock 사용료 | 종량제 | API 호출량에 따라 |

---

## 9. 리스크 및 고려사항

### 9.1 기술 리스크
- Open WebUI + Bedrock Access Gateway 조합의 대규모(2,000명) 운용 사례 확인 필요
- MCP 서버 안정성 (ONLYOFFICE MCP는 비교적 신규)
- Bedrock 모델별 도구 호출 호환성 (멀티턴 대화에서 thinking block 이슈 가능)

### 9.2 운영 리스크
- Open WebUI 업데이트 주기가 빠름 — 버전 고정 후 테스트된 버전만 배포 권장
- gVisor 코드 실행 샌드박스의 사내 보안 정책 적합성 검토 필요
- LDAP/SSO 연동 시 사내 IT 보안팀 협조 필요

### 9.3 대안 경로
- 스킬 시스템 불필요 시 LibreChat이 더 단순한 선택 (Bedrock 네이티브)
- 데스크톱 앱이 필수인 경우 Cherry Studio Enterprise 검토 (상용 라이선스)

---

## 10. 참고 자료

### Open WebUI
- GitHub: https://github.com/open-webui/open-webui
- Skills Docs: https://docs.openwebui.com/features/extensibility/plugin/tools/
- MCP Docs: https://docs.openwebui.com/features/extensibility/mcp/
- Artifacts: https://docs.openwebui.com/features/chat-features/code-execution/artifacts/
- Rich UI: https://docs.openwebui.com/features/extensibility/plugin/development/rich-ui/

### Bedrock 연결
- Bedrock Access Gateway: https://github.com/aws-samples/bedrock-access-gateway
- AWS 공식 가이드 (Open WebUI + LiteLLM + Bedrock): https://aws-samples.github.io/bedrock-litellm/20-deploy/70-open-webui/
- Open WebUI + Bedrock 연결: https://gauravve.medium.com/connecting-open-webui-to-aws-bedrock-a1f0082c8cb2

### LibreChat
- GitHub: https://github.com/danny-avila/LibreChat
- MCP: https://www.librechat.ai/docs/features/mcp
- Agents: https://www.librechat.ai/docs/features/agents
- our-chat (Bedrock + LDAP): https://github.com/dirkpetersen/our-chat

### Cherry Studio
- GitHub: https://github.com/CherryHQ/cherry-studio
- Enterprise Docs: https://github.com/CherryHQ/cherry-studio-enterprise-docs

### OnlyOffice
- MCP Server: https://github.com/ONLYOFFICE/docspace-mcp
- AI Assistants: https://www.onlyoffice.com/ai-assistants
- Anthropic 연동: https://www.onlyoffice.com/blog/2025/03/how-to-connect-anthropic-ai-models-to-onlyoffice

### 기타
- LiteLLM: https://github.com/BerriAI/litellm
- Claude Skill → LibreChat Agent 마이그레이션: https://remotefrog.com/2026/03/06/from-claude-code-skill-to-librechat-agent-in-a-jiffy/
- SKILL.md 호환 Feature Request: https://github.com/open-webui/open-webui/issues/19941
