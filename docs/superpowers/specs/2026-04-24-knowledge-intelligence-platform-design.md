# 조직 암묵지 인텔리전스 플랫폼 설계

**날짜**: 2026-04-24  
**상태**: 브레인스토밍 완료, 구현 계획 대기  
**작성자**: 브레인스토밍 세션 (bedrock-ai-agent 관리자)

---

## 1. 개요 및 목적

### 핵심 목적

어드민 대시보드에서 전 직원의 AI 대화 세션 데이터를 기반으로:

1. **조직 암묵지 인텔리전스** (Primary) — 전사 프롬프트 패턴에서 현장 암묵지가 어떻게 형성되고 변화하는지 추이·연관 분석으로 시각화
2. **구조화 프레임워크** (Secondary) — 관리자가 정의한 공식 워크플로우 템플릿을 암묵지 해석 렌즈로 활용, 갭 분석 수행
3. **개인 워크플로우 인스턴스** (Phase 3) — 사용자가 본인의 암묵지 패턴을 직접 탐색·구조화

### 이 시스템이 아닌 것

- n8n 스타일 개인 워크플로우 자동화 도구 (부가 기능에 불과)
- 단순 사용량 대시보드 확장 (기존 `/usage` 페이지와 다름)

### 핵심 인사이트

**공식 프로세스(Secondary)**와 **실제 암묵지(Primary)**의 **갭(Gap)**이 가장 가치 있는 정보다.

---

## 2. 아키텍처

```
① 기존 데이터
   prompt_audit_conversations (session_id · username · content · timestamp)
   prompt_audit_summary (category_counts JSON · 일별 집계)
         │
         │ Background Scheduler (매일 02:00 KST)
         ▼
② 암묵지 추출 (LLM Pipeline)
   Claude Haiku 4.5 배치 호출 → 개념 추출 + 관계 태깅
   → knowledge_nodes + knowledge_edges + knowledge_mentions
         │
         ▼
③ 분석 엔진
   추이 분석 · 연관 분석 · 부서 편차 분석 · 갭 분석
         │
         ▼
④ 구조화 프레임워크 (Secondary)
   workflow_templates (관리자 정의) + knowledge_taxonomy (매핑)
         │
         ▼
⑤ Admin Dashboard 신규 페이지 4개
   /analytics/knowledge-graph
   /analytics/knowledge-trends
   /analytics/knowledge-gap
   /workflows
```

---

## 3. 데이터 모델

### 3.1 신규 테이블 (7개)

#### `knowledge_nodes`
LLM이 대화에서 추출한 지식 개념 단위.

```sql
CREATE TABLE knowledge_nodes (
    id                SERIAL PRIMARY KEY,
    concept_name      VARCHAR(200) NOT NULL,
    concept_type      VARCHAR(50) NOT NULL,   -- skill|tool|domain|method|problem|topic
    normalized_name   VARCHAR(200) UNIQUE,    -- 중복 병합 기준
    description       TEXT,
    embedding         VECTOR(1536),           -- 유사도 검색용 (pgvector)
    first_seen_at     TIMESTAMP,
    last_seen_at      TIMESTAMP,
    is_active         BOOLEAN DEFAULT TRUE,
    created_at        TIMESTAMP DEFAULT NOW()
);
```

예시: `"Docker 컨테이너 최적화"` (skill), `"Python pandas"` (tool)

#### `knowledge_edges`
개념 간 관계. 그래프의 엣지.

```sql
CREATE TABLE knowledge_edges (
    id                    SERIAL PRIMARY KEY,
    source_node_id        INTEGER REFERENCES knowledge_nodes(id),
    target_node_id        INTEGER REFERENCES knowledge_nodes(id),
    edge_type             VARCHAR(50),  -- co_occurs|precedes|enables|relates_to
    weight                FLOAT DEFAULT 1.0,
    co_occurrence_count   INTEGER DEFAULT 0,
    last_seen_at          TIMESTAMP,
    created_at            TIMESTAMP DEFAULT NOW(),
    UNIQUE (source_node_id, target_node_id, edge_type)
);
```

#### `knowledge_mentions`
대화 ↔ 개념 연결. 원천 추적 및 사용자·시점 집계 기반.

```sql
CREATE TABLE knowledge_mentions (
    id                SERIAL PRIMARY KEY,
    conversation_id   INTEGER REFERENCES prompt_audit_conversations(id),
    node_id           INTEGER REFERENCES knowledge_nodes(id),
    username          VARCHAR(100),
    session_id        VARCHAR(200),
    context_snippet   VARCHAR(200),    -- 원문 30자 이내만 저장 (개인정보)
    confidence_score  FLOAT,
    mentioned_at      TIMESTAMP,
    extracted_at      TIMESTAMP DEFAULT NOW()
);
```

#### `knowledge_snapshots`
시점별 지식 지형도. 추이 분석의 핵심.

```sql
CREATE TABLE knowledge_snapshots (
    id                    SERIAL PRIMARY KEY,
    snapshot_date         DATE NOT NULL,
    granularity           VARCHAR(10) NOT NULL,  -- daily|weekly|monthly
    node_id               INTEGER REFERENCES knowledge_nodes(id),
    mention_count         INTEGER DEFAULT 0,
    unique_users          INTEGER DEFAULT 0,
    unique_sessions       INTEGER DEFAULT 0,
    department_breakdown  JSONB,         -- {"개발팀": 5, "기획팀": 2}
    prev_mention_count    INTEGER,
    growth_rate           FLOAT,         -- (current - prev) / prev
    created_at            TIMESTAMP DEFAULT NOW(),
    UNIQUE (snapshot_date, granularity, node_id)
);
```

#### `workflow_templates`
관리자 정의 공식 업무 단계 구조.

```sql
CREATE TABLE workflow_templates (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(200) NOT NULL,
    description         TEXT,
    created_by          VARCHAR(100),
    is_public           BOOLEAN DEFAULT TRUE,
    target_department   VARCHAR(100),   -- NULL = 전사
    steps               JSONB,          -- [{"id": "s1", "name": "요구수집", "desc": "..."}]
    connections         JSONB,          -- [{"from": "s1", "to": "s2", "label": "..."}]
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);
```

#### `knowledge_taxonomy`
암묵지 노드 ↔ 공식 단계 매핑. 갭 분석의 핵심.

```sql
CREATE TABLE knowledge_taxonomy (
    id                      SERIAL PRIMARY KEY,
    knowledge_node_id       INTEGER REFERENCES knowledge_nodes(id),
    workflow_template_id    INTEGER REFERENCES workflow_templates(id),
    workflow_step_id        VARCHAR(100),  -- steps[].id 참조
    mapped_by               VARCHAR(100),  -- 'auto' | username
    confidence_score        FLOAT,
    created_at              TIMESTAMP DEFAULT NOW(),
    UNIQUE (knowledge_node_id, workflow_template_id, workflow_step_id)
);
```

#### `workflow_instances`
개인 워크플로우 인스턴스 (Phase 3).

```sql
CREATE TABLE workflow_instances (
    id            SERIAL PRIMARY KEY,
    template_id   INTEGER REFERENCES workflow_templates(id),
    username      VARCHAR(100),
    name          VARCHAR(200),
    canvas_data   JSONB,    -- 노드 위치, 커스텀 연결, 메모
    is_personal   BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);
```

### 3.2 기존 테이블 변경

`prompt_audit_conversations`에 컬럼 추가:
```sql
ALTER TABLE prompt_audit_conversations
ADD COLUMN knowledge_extracted_at TIMESTAMP NULL;
```

---

## 4. LLM 추출 파이프라인

### 4.1 트리거

`auth-gateway/app/core/scheduler.py`에 APScheduler cron job 추가:

```python
@scheduler.scheduled_job('cron', hour=2, minute=0, timezone='Asia/Seoul')
async def knowledge_extraction_job():
    ...
```

### 4.2 처리 흐름

```
1. 미처리 대화 조회
   SELECT * FROM prompt_audit_conversations
   WHERE knowledge_extracted_at IS NULL
   ORDER BY timestamp ASC LIMIT 500

2. session_id로 그룹핑 (문맥 보존)

3. 세션 5~10개씩 배치 → Claude Haiku 4.5 호출
   - System prompt: "다음 AI 대화들에서 지식 개념·기술·도구·방법론을 추출하라. JSON으로 응답."
   - Output schema:
     {
       "concepts": [
         {"name": "...", "type": "skill|tool|domain|method|problem", "confidence": 0.0~1.0}
       ],
       "relationships": [
         {"source": "...", "target": "...", "type": "co_occurs|precedes|enables"}
       ]
     }
   - Prompt caching 적용 (system prompt 캐시)

4. DB 저장 (트랜잭션)
   - knowledge_nodes UPSERT ON CONFLICT(normalized_name)
   - knowledge_edges UPSERT + co_occurrence_count += 1
   - knowledge_mentions INSERT
   - prompt_audit_conversations.knowledge_extracted_at = NOW()

5. 스냅샷 생성
   - 전체 배치 완료 후 daily snapshot 생성
   - 월요일: weekly snapshot
   - 매월 1일: monthly snapshot
   - growth_rate = (현재 - 이전) / 이전
```

### 4.3 엣지 케이스

| 상황 | 처리 |
|------|------|
| LLM 응답 실패 | 재시도 3회 → 실패 시 해당 배치 skip, 다음 날 재처리 |
| JSON 파싱 오류 | fallback: `category_counts` 기반 단순 매핑 |
| 비용 폭증 방지 | 1회 실행 max 500개 대화 상한선 |
| 개인정보 | `context_snippet` 30자 이내만 저장, 원문 미보관 |

---

## 5. 분석 엔진

### 5.1 추이 분석
**소스**: `knowledge_snapshots`

| 분류 | 조건 | 의미 |
|------|------|------|
| Emerging | growth_rate > +30% AND first_seen < 4주 | 조직이 새로 배우는 것 |
| Rising | growth_rate > +15% | 관심 증가 영역 |
| Stable | \|growth_rate\| < 15% | 안정적 역량 |
| Declining | growth_rate < -20% (연속 3주) | 사라지는 암묵지 |

**API**: `GET /api/v1/knowledge/trends?granularity=weekly&weeks=12`

### 5.2 연관 분석
**소스**: `knowledge_edges`

Market Basket Analysis 방식:
- **Support**: P(A) = mentions(A) / total_mentions
- **Confidence**: P(B|A) = co_occurrences(A,B) / mentions(A)
- **Lift**: P(A,B) / (P(A) × P(B)) — 우연 이상의 연관성

**API**: `GET /api/v1/knowledge/associations?min_lift=1.5&min_support=0.05`

### 5.3 부서 편차 분석
**소스**: `knowledge_snapshots.department_breakdown`

- 팀별 고유 지식 (특정 팀에만 집중 → 지식 사일로 탐지)
- 전사 공통 지식 (모든 팀 출현 → 조직 공통 역량)
- 지식 전파 경로 (first_seen 팀 기준 전파 추적)
- 팀 지식 다양성 지수 (Shannon entropy)

**API**: `GET /api/v1/knowledge/departments?period=monthly`

### 5.4 갭 분석 (핵심)
**소스**: `knowledge_taxonomy` + `workflow_templates` + `knowledge_nodes`

| 유형 | 정의 | 시사점 |
|------|------|--------|
| 사문화된 프로세스 | 템플릿 단계는 있으나 knowledge_mentions 희박 | 현장이 따르지 않는 공식 절차 |
| 숨겨진 암묵지 | knowledge_nodes 빈번 등장 but 어떤 taxonomy에도 미매핑 | 발굴해야 할 현장 지식 |
| 커버리지율 | 매핑된 노드 / 전체 활성 노드 | 공식화 수준 측정 |

**API**: `GET /api/v1/knowledge/gap?template_id={id}`  
**Returns**: `coverage_rate(%)`, `shadow_processes[]`, `undocumented_knowledge[]`

---

## 6. Admin Dashboard 신규 페이지

### `/analytics/knowledge-graph`
- React Flow 기반 인터랙티브 노드 그래프
- 노드 크기 = 언급 빈도, 엣지 굵기 = 연결 강도
- 필터: 기간 · 부서 · concept_type · edge_type
- 노드 클릭 → 상세 패널 (연관 개념, 언급 사용자, 샘플 대화)

### `/analytics/knowledge-trends`
탭 구성:
- **추이 차트**: Emerging/Rising/Declining 개념 목록 + 스파크라인
- **Sankey Flow**: 단계 간 전환 흐름
- **타임라인**: 팀별·날짜별 Gantt 형태
- **연관 규칙**: Association rules 테이블 (Lift 기준 정렬)

### `/analytics/knowledge-gap`
- 워크플로우별 커버리지 바 (coverage_rate)
- 미분류 암묵지 Top N (빈도 기준)
- 사문화된 프로세스 목록

### `/workflows`
- 워크플로우 템플릿 목록 + 신규 생성
- React Flow 캔버스 (드래그·연결·메모)
- knowledge_taxonomy 매핑 관리 패널
- (Phase 3) 사용자 개인 인스턴스 뷰

---

## 7. 구현 순서

### Phase 1 — 암묵지 파이프라인 + 그래프 뷰 (~2주)

**Backend**
- [ ] Alembic 마이그레이션: 7개 신규 테이블 + `knowledge_extracted_at` 컬럼
- [ ] `knowledge_extraction_job()` 스케줄러 (scheduler.py 확장)
- [ ] Claude Haiku 4.5 배치 호출 모듈 (bedrock_proxy 재사용)
- [ ] `GET /api/v1/knowledge/graph`
- [ ] `GET /api/v1/knowledge/trends`

**Frontend**
- [ ] `reactflow` 패키지 설치
- [ ] `/analytics/knowledge-graph` 페이지
- [ ] `/analytics/knowledge-trends` 페이지 (추이 탭)
- [ ] 노드 클릭 상세 패널 컴포넌트

**목표**: 기존 대화 데이터에서 지식 그래프 자동 생성 및 시각화

---

### Phase 2 — 분석 엔진 + 갭 분석 + 워크플로우 빌더 (~2주)

**Backend**
- [ ] 연관 분석 (Support/Confidence/Lift 집계)
- [ ] 부서 편차 분석 API
- [ ] 워크플로우 템플릿 CRUD API
- [ ] `knowledge_taxonomy` 매핑 API
- [ ] `GET /api/v1/knowledge/gap`
- [ ] `GET /api/v1/knowledge/associations`
- [ ] `GET /api/v1/knowledge/departments`
- [ ] 주간·월간 snapshot job

**Frontend**
- [ ] `/analytics/knowledge-gap` 페이지
- [ ] Sankey 다이어그램 컴포넌트
- [ ] 부서별 히트맵
- [ ] `/workflows` 템플릿 관리 (관리자용 React Flow 캔버스)

**목표**: 암묵지 ↔ 공식 프로세스 갭 분석 가능

---

### Phase 3 — 개인 워크플로우 인스턴스 (~1.5주)

- [ ] `workflow_instances` 개인 캔버스 (어드민 + 사용자 허브)
- [ ] 템플릿 → 개인 인스턴스 복사
- [ ] 개인 암묵지 노드 ↔ 워크플로우 단계 연결 시각화

**목표**: 개인이 본인의 암묵지 패턴을 직접 탐색·구조화

---

## 8. 기술 스택 추가 사항

| 항목 | 선택 | 이유 |
|------|------|------|
| 그래프 캔버스 | `@xyflow/react` (React Flow v12+) | 노드 드래그·연결 UI, MIT 라이선스 |
| 차트 | `recharts` | 이미 사용 중 |
| LLM | Claude Haiku 4.5 | 비용 최소화 (~1/10), 추출 작업에 충분 |
| 스케줄러 | 기존 APScheduler | scheduler.py 확장 |
| Bedrock 호출 | 기존 bedrock_proxy | 재사용 |
| DB 마이그레이션 | Alembic | 기존 패턴 유지 |

---

## 9. 미결 사항

- `embedding VECTOR(1536)` 사용 여부 — pgvector 확장 활성화 여부 확인 필요 (없으면 normalized_name 텍스트 매칭으로 대체)
- 개인 워크플로우(Phase 3)가 어드민 대시보드 안에 있는지, 사용자 허브(Pod) 안에 있는지 결정 필요
- LLM 추출 비용 한도 설정 (월 예산 기준 max 대화 처리 수 계산 필요)
- 모든 `/api/v1/knowledge/*` 엔드포인트는 기존 `require_admin` 의존성으로 관리자 전용 처리 (명시 필요)
