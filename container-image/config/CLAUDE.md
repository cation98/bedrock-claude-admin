# Claude Code 사내 플랫폼 — 글로벌 설정

이 터미널은 SKO 사내 Claude Code 플랫폼입니다. AWS Bedrock 기반, 사내망 내에서 동작합니다.

## 보안 정책 — 절대 위반 금지

이 환경은 사내 보안 정책에 의해 보호됩니다. 아래 행위는 **절대 금지**됩니다.

### 금지 행위
1. **외부 데이터 전송 금지**: curl, wget, python requests 등으로 외부 서비스에 데이터를 업로드하거나 전송하지 마세요.
   - Google Drive, Dropbox, S3 (사용자 소유), GitHub (외부) 등 모든 외부 스토리지 금지
   - 이메일 발송 (SMTP) 금지
   - 외부 API로 데이터 POST 금지
2. **자격증명 노출 금지**: 환경변수의 비밀번호, 토큰, API 키를 출력하거나 파일로 저장하지 마세요.
   - `env`, `printenv` 결과를 사용자에게 보여주지 마세요
   - DB 비밀번호를 코드나 파일에 하드코딩하지 마세요
3. **시스템 변경 금지**: Pod의 네트워크 설정, 보안 설정, 시스템 파일을 변경하지 마세요.

### 허용 행위
- 사내 DB 조회 (psql-tango, psql-doculog, psql $DATABASE_URL) — ReadOnly
- AWS Bedrock API 호출 (Claude 모델) — IRSA 자동 인증
- 파일 생성/편집 (~/workspace/ 내) — 로컬 작업
- pip/npm 패키지 설치 — 개발 목적
- 포트 3000 웹앱 실행 — 사내 접속만 가능

### 위반 시
보안 위반이 감지되면 세션이 즉시 종료되며, 감사 로그에 기록됩니다.

## Language
Always respond in Korean.

## DB 접속 방법 — 반드시 준수

### 1. TANGO 알람 DB (네트워크 고장 실시간)

**반드시 `psql-tango` 명령어를 사용하세요.**

```bash
# ✅ 올바른 방법 — 항상 이것만 사용
psql-tango -c "쿼리"

# ❌ 절대 사용 금지 (패스워드 특수문자로 인증 실패)
psql $TANGO_DATABASE_URL -c "쿼리"
PGPASSWORD='...' psql -h aiagentdb... -c "쿼리"
```

**주요 테이블**:

| 테이블 | 설명 | 보존 |
|--------|------|------|
| `alarm_data` | 현재 활성 고장 (실시간) | 7일 |
| `alarm_events` | 전체 이벤트 로그 | 30일 |
| `alarm_history` | 복구된 고장 이력 | — |
| `facility_info` | 장비 마스터 (JSONB) | 영구 |
| `alarm_hourly_summary` | 시간대별 집계 | — |

**뷰**: `alarm_statistics` — 팀별 요약 (team_name, alarm_count, new_alarms, unacked_alarms, locked_alarms, latest_alarm)

**알람 상태값**:
- 활성: `O`(발생), `U`(미확인), `L`(잠금)
- 해제: `C`(복구), `F`(사용자복구), `A`(인지), `D`(삭제)

**주요 컬럼**:

| 컬럼 | 설명 | 예시 |
|------|------|------|
| `OP_TEAM_ORG_NM` | 운용팀명 | 김해품질개선팀 |
| `OP_HDOFC_ORG_NM` | 본부명 | 경남담당 |
| `EQP_NM` | 장비명 | SKT-BS-12345 |
| `FALT_OCCR_LOC_CTT` | 고장위치 | 경남 김해시 ... |
| `EVT_TIME` | 발생시각 | 2026-03-23 12:00:00 |
| `ALM_STAT_VAL` | 상태 | O, U, L, C, F, A, D |
| `ALM_DESC` | 알람 설명 | Link Down |
| `MCP_NM` | 시/도 | 경상남도 |
| `SGG_NM` | 시/군/구 | 김해시 |
| `LDONG_NM` | 동 | 내동 |
| `EQP_ID` | 장비ID (facility_info JOIN키) | EQP001 |

**자주 쓰는 쿼리**:
```sql
-- 팀별 활성 고장 현황
psql-tango -c "SELECT * FROM alarm_statistics ORDER BY alarm_count DESC;"

-- 특정 본부/팀 필터
psql-tango -c "SELECT OP_TEAM_ORG_NM, COUNT(*) FROM alarm_data WHERE OP_HDOFC_ORG_NM LIKE '%경남%' GROUP BY OP_TEAM_ORG_NM ORDER BY COUNT(*) DESC;"

-- 최근 24시간 추이
psql-tango -c "SELECT date_trunc('hour', received_at) AS hour, COUNT(*) FROM alarm_events WHERE received_at > NOW() - INTERVAL '24 hours' GROUP BY hour ORDER BY hour;"
```

### 2. 안전관리 DB (Safety)

```bash
# ✅ 올바른 방법
psql $DATABASE_URL -c "쿼리"
```

- 읽기 전용 (SELECT만 가능)
- DB: safety
- 테이블 목록: `psql $DATABASE_URL -c "\dt"`

**주요 테이블 (업무별)**:

| 분류 | 테이블 | 설명 |
|------|--------|------|
| **TBM** | `safety_activity_tbmactivity` | TBM 활동 기록 |
| | `safety_activity_tbmactivity_companion` | TBM 동행자 |
| | `safety_activity_tbmactivityimages` | TBM 사진 |
| **작업정보** | `safety_activity_workinfo` | 작업 정보 (region_sko, team 등) |
| | `safety_activity_workstatus` | 작업 상태 |
| | `safety_activity_workstatushistory` | 작업 상태 이력 |
| | `safety_activity_worktype` | 작업 유형 |
| **작업중지** | `safety_activity_workstophistory` | 작업 중지 이력 |
| | `safety_activity_workstophistoryimages` | 작업 중지 사진 |
| **순찰점검** | `safety_activity_patrolsafetyinspection` | 순찰 안전점검 |
| | `safety_activity_patrolsafetyinspectchecklist` | 점검 체크리스트 |
| | `safety_activity_patrolsafetyinspectiongoodandbad` | 양호/불량 판정 |
| | `safety_activity_patrolsafetyjointinspection` | 합동 점검 |
| **주간계획** | `safety_activity_weeklyworkplanfrombp` | BP별 주간 작업계획 |
| | `safety_activity_weeklyworkplanperskoregion` | SKO 담당별 주간계획 |
| | `safety_activity_weeklyworkplanperskoteam` | SKO 팀별 주간계획 |
| **안전등급(SHE)** | `she_measurement_sherecord` | SHE 측정 기록 |
| | `she_measurement_shecategory` | SHE 카테고리 |
| | `she_measurement_sheitemscore` | SHE 항목 점수 |
| **컴플라이언스** | `compliance_check_checklistrecord` | 컴플라이언스 점검 기록 |
| | `compliance_check_checklistitem` | 점검 항목 |
| **위험성평가** | `committee_workriskassessment` | 작업 위험성 평가 |
| **게시판** | `board_post` | 게시글 |
| | `board_comment` | 댓글 |
| | `board_file` | 첨부파일 |
| **사용자** | `auth_user` | Django 사용자 (username=사번) |
| | `accounts_userprofile` | 사용자 프로필 (region_name, team_name, job_name) |
| **조직** | `sysmanage_region` | 담당 조직 |
| | `sysmanage_teamregion` | 팀 조직 |
| | `sysmanage_companymaster` | 협력사 마스터 |

**자주 쓰는 Safety 쿼리**:
```sql
-- 오늘 TBM 건수 (담당별)
psql $DATABASE_URL -c "SELECT w.region_sko, COUNT(*) FROM safety_activity_tbmactivity t JOIN safety_activity_workinfo w ON t.work_id_id = w.id WHERE DATE(t.created_at) = CURRENT_DATE GROUP BY w.region_sko ORDER BY COUNT(*) DESC;"

-- 작업 현황
psql $DATABASE_URL -c "SELECT status, COUNT(*) FROM safety_activity_workstatus GROUP BY status;"

-- 순찰점검 현황
psql $DATABASE_URL -c "SELECT COUNT(*) FROM safety_activity_patrolsafetyinspection WHERE DATE(created_at) = CURRENT_DATE;"
```

### 3. Opark 업무일지 DB (TANGO DB와 동일 서버)

Opark 테이블은 TANGO와 같은 DB(`postgres`)에 있으므로 `psql-tango`로 접근합니다.

```bash
psql-tango -c "SELECT * FROM opark_daily_report LIMIT 5;"
```

**Opark 테이블**:

| 테이블 | 설명 |
|--------|------|
| `opark_daily_report` | OPAC 47컬럼 업무일지 (1분 주기 upsert) |
| `report_embeddings` | pgvector 768dim 벡터 (ko-sroberta) |
| `report_ontology` | 5단계 업무 분류 트리 (level/code/parent_code) |
| `report_alarm_matches` | 알람-업무 유사도 매칭 결과 |
| `opark_b2bequipmaster` | B2B 장비 마스터 |
| `opark_cmsequipmaster` | CMS 장비 마스터 |
| `opark_equipmaster` | 장비 마스터 |
| `opark_evchrgequipmaster` | 전기차 충전 장비 |
| `opark_fronthaulequipmaster` | Fronthaul 장비 |

### 4. Docu-Log 문서활동 분석 DB

문서활동 로그 267일간 4,616,363건 분석 결과. 부서별 업무 현황, 업무 중복, 반복 패턴 질의 가능.

```bash
# ✅ 올바른 방법
psql-doculog -c "쿼리"
```

**주요 테이블**:

| 테이블 | 설명 | 건수 |
|--------|------|------|
| `document_logs` | 문서활동 로그 원본 + 분석 컬럼 | 4,616,363 |
| `task_embeddings` | 업무명 임베딩 벡터 (768dim) | 359,968 |
| `mv_pre_reorg` | 2025년 개편 전 데이터 뷰 | 4,037,324 |

**핵심 컬럼**:

| 컬럼 | 설명 |
|------|------|
| `fn_task_normalized` | 핵심 분석 단위 — 날짜/버전 제거된 업무명 |
| `fn_doc_type` | 문서 유형 (현황/보고서/점검/계획 등 13종) |
| `department` | 소속 부서 (192개) |
| `dept_function` | 부서 기능 (품질혁신, Access관제 등) |
| `dept_region` | 부서 지역 (서울, 경남 등 18개) |
| `log_type` | 활동 유형 (편집, 생성, 읽기 등) |

**자주 쓰는 쿼리**:
```sql
-- 부서기능별 주요 업무 Top 10
psql-doculog -c "SELECT fn_task_normalized, COUNT(*) FROM document_logs WHERE dept_function = '품질혁신' GROUP BY 1 ORDER BY 2 DESC LIMIT 10;"

-- 부서간 업무 중복
psql-doculog -c "SELECT dept_function, COUNT(DISTINCT fn_task_normalized) FROM document_logs GROUP BY 1 ORDER BY 2 DESC;"

-- 유사 업무 시맨틱 검색
psql-doculog -c "SET hnsw.ef_search = 100; SELECT e2.fn_task_normalized, ROUND((1-(e1.embedding<=>e2.embedding))::numeric,4) AS sim FROM task_embeddings e1 CROSS JOIN LATERAL (SELECT fn_task_normalized,embedding FROM task_embeddings WHERE fn_task_normalized!=e1.fn_task_normalized ORDER BY embedding<=>e1.embedding LIMIT 5) e2 WHERE e1.fn_task_normalized='안전점검';"
```

## 업무 키워드 → 데이터 소스 매핑

| 키워드 | DB | 접속 명령 | 주요 테이블 |
|--------|-----|----------|------------|
| TBM, TBM건수, 작업허가 | **Safety DB** | `psql $DATABASE_URL` | `safety_activity_tbmactivity`, `safety_activity_workinfo` |
| 시설, 장비, 설비 | **Safety/TANGO DB** | 양쪽 확인 | `opark_equipmaster`, `facility_info` |
| 고장, 알람, 장애 | **TANGO DB** | `psql-tango` | `alarm_data`, `alarm_events`, `alarm_statistics` |
| 업무일지, Opark, 일일보고 | **TANGO DB** | `psql-tango` | `opark_daily_report` |
| 안전점검, 순찰 | **Safety DB** | `psql $DATABASE_URL` | `safety_activity_patrolsafetyinspection_*` |
| 작업상태, 작업중지 | **Safety DB** | `psql $DATABASE_URL` | `safety_activity_workstatus`, `safety_activity_workstophistory` |
| 문서활동, 문서로그, 업무패턴 | **Docu-Log DB** | `psql-doculog` | `document_logs`, `task_embeddings` |

### DB 공통 규칙
1. **TANGO DB → `psql-tango -c "쿼리"`** (절대 `$TANGO_DATABASE_URL` 직접 사용 금지)
2. **Safety DB → `psql $DATABASE_URL -c "쿼리"`**
3. **Docu-Log DB → `psql-doculog -c "쿼리"`**
4. 대량 데이터 → `LIMIT` 사용
5. 한글 데이터 포함

## 웹 터미널 조작 안내

이 환경은 웹 브라우저 기반 터미널(ttyd)입니다.
- **슬래시 명령(`/`) 목록 탐색**: `j`(아래), `k`(위) 키 사용 (화살표 키는 웹 터미널에서 동작하지 않을 수 있음)
- **선택**: Enter 키

## 사용 가능한 도구

- **psql-tango**: TANGO 알람 DB 접속
- **psql-doculog**: Docu-Log 문서활동 분석 DB 접속
- **psql $DATABASE_URL**: 안전관리 DB 접속
- **Python 3**: pandas, matplotlib, openpyxl 설치됨
- **git**: 버전 관리
- **AWS CLI**: AWS 리소스 조회

## 웹앱 개발 및 접속

Pod에서 웹앱(대시보드 등)을 만들면 브라우저에서 접속할 수 있습니다.

### 웹앱 보안 정책 — 절대 위반 금지

웹앱은 **사내 데이터 시각화 및 업무 도구** 목적으로만 허용됩니다. 아래 행위는 **절대 금지**이며, 요청 시 거부해야 합니다.

#### 금지 1: 자체 사용자 인증/관리 구현 금지
- 웹앱 내에 로그인 폼, 회원가입, 세션 관리, JWT 발급 등 **자체 인증 시스템을 구현하지 마세요.**
- 사용자 인증은 플랫폼(SSO + 2FA)이 Ingress 레벨에서 처리합니다.
- `X-Auth-Username` 헤더로 인증된 사용자 정보를 받아 표시만 가능합니다.
- 위반 예시: `POST /login`, `POST /register`, password 입력 폼, 자체 토큰 발급

#### 금지 2: API 전용 서비스 금지
- **반드시 사용자가 브라우저에서 볼 수 있는 HTML UI(프론트엔드)가 포함되어야 합니다.**
- REST API만 제공하고 프론트엔드가 없는 서비스는 생성할 수 없습니다.
- 위반 예시: JSON만 반환하는 엔드포인트만 구성, GraphQL 서버만 운영, Webhook 수신 서버

#### 금지 3: 데이터 우회 허브 금지
- 웹앱을 **사내 데이터를 외부 도구로 전달하는 중계 서버**로 사용할 수 없습니다.
- Excel/PowerPoint 등 Office 제품, Tableau, Power BI 등에서 데이터를 가져갈 수 있는 API 엔드포인트를 만들지 마세요.
- OData, CSV 다운로드 API, RSS 피드, 외부 연동용 API 등 **외부 소프트웨어가 자동으로 데이터를 수집할 수 있는 인터페이스**를 만들지 마세요.
- 위반 예시: `/api/export/csv`, `/api/data.json` (외부 도구 연동 목적), CORS 허용 API, Power Query 연결 API

#### 위반 감지 시 대응
위의 의도가 감지되면 **즉시 작업을 중단**하고 다음을 사용자에게 안내하세요:

> ⚠️ **보안 정책 위반**: 이 웹앱은 사내 데이터 시각화 및 업무 도구 목적으로만 사용 가능합니다.
> 자체 인증, API 전용 서비스, 외부 도구 연동용 데이터 허브는 보안 정책상 구현할 수 없습니다.
> 데이터 활용이 필요하면 관리자에게 공식 데이터 파이프라인을 요청하세요.

### 웹앱 실행 규칙 — 반드시 포트 3000 사용

```bash
# ✅ 올바른 방법 — 포트 3000으로 실행
python3 -m uvicorn app:app --host 0.0.0.0 --port 3000

# ❌ 다른 포트 사용 금지 (Ingress가 3000만 라우팅)
python3 -m uvicorn app:app --port 8200  # 외부 접속 불가
```

### 접속 URL
웹앱을 포트 3000으로 실행하면 브라우저에서 아래 주소로 접속:
```
https://claude.skons.net/app/{HOSTNAME}/
```
- `{HOSTNAME}`은 Pod 이름 (예: `claude-terminal-n1102359`)
- 환경변수 `$HOSTNAME`으로 확인 가능

### 사전 설치된 패키지 (pip 설치 불필요)
- **fastapi**, **uvicorn** — 웹 프레임워크
- **psycopg2-binary** — PostgreSQL 드라이버
- **jinja2** — HTML 템플릿
- **pandas**, **matplotlib**, **openpyxl** — 데이터 분석/차트/엑셀
- **python-multipart** — 파일 업로드

### 웹앱 URL 규칙 (무한 리다이렉트 방지)

웹앱은 `/app/{HOSTNAME}/` 경로 뒤에서 실행됩니다. **폼/링크에서 절대 경로(`/`)를 사용하면 로그인 페이지로 이동하는 무한 루프가 발생합니다.**

```html
<!-- 템플릿 <head>에 반드시 추가 -->
<base href="/app/{{ hostname }}/">

<!-- 폼은 상대 경로 사용 -->
<form action="">        ✅
<form action="/">       ❌ (로그인 페이지로 이동)
```

서버에서 `hostname = os.environ.get("HOSTNAME")` 전달 필수.

### 웹앱 예시 (FastAPI 대시보드)
```python
# app.py
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def index():
    return "<h1>대시보드</h1>"

# 실행: python3 -m uvicorn app:app --host 0.0.0.0 --port 3000
# 접속: https://claude.skons.net/app/{HOSTNAME}/
```

## 텔레그램 봇 연동

사내 텔레그램 봇(@SKO_Claude_Bot)이 운영 중입니다.

### 사용 방법
1. 텔레그램에서 @SKO_Claude_Bot 검색 또는 링크 접속
2. `/등록 사번` 입력 (예: `/등록 N1102359`)
3. 자유롭게 질문 (한국어)

### 단체방 사용
- 봇을 단체방에 초대
- `@SKO_Claude_Bot 질문` 형태로 멘션하여 사용
- 개인 DM에서는 멘션 없이 바로 질문 가능

### 텔레그램에서 가능한 것
- DB 조회 결과 분석 (고장 현황, TBM 등)
- 데이터 요약/통계
- SMS 발송 요청
- 일반 질문/번역/요약

### 텔레그램에서 불가능한 것
- 코드 실행 (Pod 터미널 사용)
- 파일 생성/다운로드 (Pod 파일 관리 사용)
- 대시보드 구현 (Pod 웹앱 사용)

## 파일 관리

- 업로드된 파일: `~/workspace/uploads/`
- 보고서 저장: `~/workspace/reports/`
- 엑셀 저장: `~/workspace/exports/`
- 파일 다운로드: 브라우저 /files/ 페이지
