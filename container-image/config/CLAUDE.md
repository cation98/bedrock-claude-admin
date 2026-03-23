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
- 사내 DB 조회 (psql-tango, psql $DATABASE_URL) — ReadOnly
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

### DB 공통 규칙
1. **TANGO DB → `psql-tango -c "쿼리"`** (절대 `$TANGO_DATABASE_URL` 직접 사용 금지)
2. **Safety DB → `psql $DATABASE_URL -c "쿼리"`**
3. 대량 데이터 → `LIMIT` 사용
4. 한글 데이터 포함

## 웹 터미널 조작 안내

이 환경은 웹 브라우저 기반 터미널(ttyd)입니다.
- **슬래시 명령(`/`) 목록 탐색**: `j`(아래), `k`(위) 키 사용 (화살표 키는 웹 터미널에서 동작하지 않을 수 있음)
- **선택**: Enter 키

## 사용 가능한 도구

- **psql-tango**: TANGO 알람 DB 접속
- **psql $DATABASE_URL**: 안전관리 DB 접속
- **Python 3**: pandas, matplotlib, openpyxl 설치됨
- **git**: 버전 관리
- **AWS CLI**: AWS 리소스 조회

## 웹앱 개발 및 접속

Pod에서 웹앱(대시보드, API 등)을 만들면 브라우저에서 접속할 수 있습니다.

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

## 파일 관리

- 업로드된 파일: `~/workspace/uploads/`
- 보고서 저장: `~/workspace/reports/`
- 엑셀 저장: `~/workspace/exports/`
- 파일 다운로드: 브라우저 /files/ 페이지
