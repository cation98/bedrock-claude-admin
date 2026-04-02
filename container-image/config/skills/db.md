---
name: db
description: 사내 데이터베이스(TANGO 알람, Safety)에 접속하여 데이터를 조회합니다. DB 접속, 쿼리, 데이터 분석 요청 시 사용합니다.
---

# 데이터베이스 접속 스킬

## 핵심 규칙 — 반드시 준수

### TANGO 알람 DB (네트워크 고장)

**절대 `$TANGO_DATABASE_URL` 환경변수를 직접 사용하지 마세요!**
패스워드에 특수문자가 포함되어 인증 오류가 발생합니다.

```bash
# ✅ 올바른 방법 — 항상 psql-tango 명령어 사용
psql-tango -c "쿼리"

# ❌ 잘못된 방법 — 절대 사용 금지
psql $TANGO_DATABASE_URL -c "쿼리"
PGPASSWORD='...' psql -h aiagentdb... -c "쿼리"
```

### Safety DB (안전관리)

```bash
# ✅ 올바른 방법
psql $DATABASE_URL -c "쿼리"
```

## TANGO DB 스키마 참조

### alarm_data (현재 활성 고장, 7일 보존)
```sql
-- 팀별 현황
SELECT OP_TEAM_ORG_NM, COUNT(*) FROM alarm_data GROUP BY OP_TEAM_ORG_NM ORDER BY COUNT(*) DESC;

-- 특정 팀/본부 필터
SELECT * FROM alarm_data WHERE OP_TEAM_ORG_NM LIKE '%김해%';
SELECT * FROM alarm_data WHERE OP_HDOFC_ORG_NM LIKE '%경남%';
```

### alarm_statistics (뷰 — 팀별 요약)
```sql
-- 전체 팀 요약 (가장 빠른 조회)
SELECT * FROM alarm_statistics ORDER BY alarm_count DESC;
```

### alarm_events (전체 이벤트 로그, 30일)
```sql
-- 최근 24시간 추이
SELECT date_trunc('hour', received_at) AS hour, COUNT(*)
FROM alarm_events
WHERE received_at > NOW() - INTERVAL '24 hours'
GROUP BY hour ORDER BY hour;
```

### alarm_history (복구 이력)
```sql
-- 최근 복구 건
SELECT EQP_NM, FALT_OCCR_LOC_CTT, recovered_at
FROM alarm_history ORDER BY recovered_at DESC LIMIT 10;
```

### facility_info (장비 마스터, JSONB)
```sql
-- 장비 정보 조회 (alarm_events와 JOIN)
SELECT e.EQP_NM, e.EVT_TIME, f.data->>'facility_type' AS 장비유형
FROM alarm_events e JOIN facility_info f ON e.EQP_ID = f.EQP_ID
LIMIT 10;
```

## 주요 컬럼 설명

| 컬럼 | 설명 | 예시 |
|------|------|------|
| `OP_TEAM_ORG_NM` | 운용팀명 | 김해품질개선팀 |
| `OP_HDOFC_ORG_NM` | 본부명 | 경남담당 |
| `EQP_NM` | 장비명 | SKT-BS-12345 |
| `FALT_OCCR_LOC_CTT` | 고장위치 | 경남 김해시 ... |
| `EVT_TIME` | 발생시각 | 2026-03-23 12:00:00 |
| `ALM_STAT_VAL` | 상태 | O(발생), C(복구) |
| `ALM_DESC` | 알람 설명 | Link Down |
| `MCP_NM` | 시/도 | 경상남도 |
| `SGG_NM` | 시/군/구 | 김해시 |

## 알람 상태값

- **활성**: `O`(발생/Occurred), `U`(미확인/Unacknowledged), `L`(잠금/Locked)
- **해제**: `C`(복구/Cleared), `F`(사용자복구), `A`(인지/Acknowledged), `D`(삭제/Deleted)

## SQLite 데이터베이스 (업로드된 데이터)

사용자가 업로드한 대용량 Excel/CSV는 SQLite로 자동 변환됩니다.

### 데이터베이스 발견 — 반드시 먼저 확인

```bash
# 1. 내 SQLite DB 목록 + 스키마 확인
ls ~/workspace/shared-data/*.sqlite 2>/dev/null
cat ~/workspace/shared-data/*.schema.md 2>/dev/null

# 2. 팀에서 공유받은 SQLite DB 확인
ls ~/workspace/team/*/*.sqlite 2>/dev/null
cat ~/workspace/team/*/*.schema.md 2>/dev/null
```

`.schema.md` 파일에 테이블명, 컬럼명, 타입, 샘플 데이터가 기록되어 있습니다.
**데이터 분석 요청 시 schema.md를 먼저 읽고 적절한 SQL을 작성하세요.**

### SQLite 쿼리 방법

```bash
# 내 데이터 (읽기+쓰기)
sqlite3 ~/workspace/shared-data/erp.sqlite "SELECT * FROM sheet1 LIMIT 10;"

# 팀 공유 데이터 (읽기 전용)
sqlite3 ~/workspace/team/n1001064/erp/erp.sqlite "SELECT * FROM sheet1 WHERE 부서='강북Access담당';"

# 테이블 목록 확인
sqlite3 ~/workspace/shared-data/erp.sqlite ".tables"

# 컬럼 확인
sqlite3 ~/workspace/shared-data/erp.sqlite ".schema sheet1"
```

### 공유 구조

| 경로 | 권한 | 설명 |
|------|------|------|
| `~/workspace/shared-data/` | 읽기+쓰기 | 내가 업로드/생성한 데이터 |
| `~/workspace/team/{소유자사번}/` | **읽기 전용** | 다른 사용자가 공유한 데이터 |

- 공유 데이터에 쓰기 시도 시 `Read-only file system` 에러 발생 (정상)
- 공유 데이터 수정이 필요하면 소유자에게 요청하거나, 소유자의 웹앱을 통해 입력

### 다중 파일 분석 — 자동 SQLite 병합 규칙

**여러 Excel/CSV 파일을 동시에 분석할 때 반드시 아래 절차를 따르세요:**

1. 대상 파일의 **합산 크기를 먼저 계산**:
```bash
du -sh ~/workspace/uploads/TBM*.xlsx
```

2. **합산 10MB 초과** 또는 **파일 2개 이상** 시 → SQLite에 병합 후 SQL로 분석:
```python
import pandas as pd, sqlite3, glob, os

# 대상 파일 탐색
files = glob.glob(os.path.expanduser("~/workspace/uploads/TBM*.xlsx"))
print(f"대상: {len(files)}개, 합산: {sum(os.path.getsize(f) for f in files)/1024/1024:.1f}MB")

# SQLite에 병합
db_path = os.path.expanduser("~/workspace/shared-data/tbm-combined.sqlite")
conn = sqlite3.connect(db_path)
for f in files:
    name = os.path.splitext(os.path.basename(f))[0].replace(" ", "_").lower()
    df = pd.read_excel(f)
    df.to_sql(name, conn, if_exists="replace", index=False)
    print(f"  {name}: {len(df)}행")

# 전체 통합 테이블도 생성
all_df = pd.concat([pd.read_excel(f) for f in files], ignore_index=True)
all_df.to_sql("all_data", conn, if_exists="replace", index=False)
print(f"  all_data: {len(all_df)}행 (통합)")
conn.close()

# 스키마 생성
```

3. **스키마 파일 생성** (Claude가 구조를 인식하도록):
```bash
python3 -c "
import sqlite3, os
conn = sqlite3.connect(os.path.expanduser('~/workspace/shared-data/tbm-combined.sqlite'))
cur = conn.cursor()
tables = cur.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
with open(os.path.expanduser('~/workspace/shared-data/tbm-combined.schema.md'), 'w') as f:
    f.write('# tbm-combined.sqlite\n\n')
    for (tbl,) in tables:
        cnt = cur.execute(f'SELECT COUNT(*) FROM [{tbl}]').fetchone()[0]
        cols = cur.execute(f'PRAGMA table_info([{tbl}])').fetchall()
        f.write(f'## {tbl} ({cnt:,} rows)\n| Column | Type |\n|--------|------|\n')
        for c in cols:
            f.write(f'| {c[1]} | {c[2] or \"TEXT\"} |\n')
        f.write('\n')
conn.close()
print('스키마 생성 완료')
"
```

4. 이후 **SQL 쿼리로 분석** (pandas 재로딩 금지):
```bash
sqlite3 ~/workspace/shared-data/tbm-combined.sqlite "SELECT 팀명, COUNT(*) FROM all_data GROUP BY 팀명 ORDER BY COUNT(*) DESC;"
```

**핵심 원칙**: 파일을 `pd.read_excel()`로 매번 읽지 마세요. **한 번만 읽어서 SQLite에 저장**하고, 이후는 SQL로만 분석합니다.

### SQLite vs PostgreSQL 선택 기준

| 상황 | 사용 DB | 이유 |
|------|---------|------|
| 업로드된 Excel/CSV 분석 | **SQLite** | 자동 변환됨, 로컬 쿼리 |
| 실시간 고장 현황 | **TANGO (psql-tango)** | 실시간 데이터 |
| 안전관리/TBM | **Safety (psql $DATABASE_URL)** | 운영 데이터 |
| 문서활동 분석 | **Docu-Log (psql-doculog)** | 대용량 벡터 검색 |

## Opark 업무일지 — 실시간 vs 과거 데이터

**사용자가 업무일지/Opark/일일보고 데이터를 요청하면 반드시 기간을 먼저 확인하세요.**

```
사용자: "업무일지 분석해줘" 또는 "3월 Opark 현황"
→ 반드시 질문: "어느 기간의 데이터를 조회할까요?"
```

| 테이블 | 건수 | 용도 |
|--------|------|------|
| `opark_daily_report` | ~183K | **실시간** (최근, 1분 주기 갱신) |
| `opark_daily_archive` | ~1.8M | **과거** 아카이브 (이전 기간 전체) |

```sql
-- 실시간 (오늘/최근)
psql-tango -c "SELECT COUNT(*) FROM opark_daily_report;"

-- 과거 (특정 기간)
psql-tango -c "SELECT COUNT(*) FROM opark_daily_archive WHERE archived_at >= '2026-03-01' AND archived_at < '2026-04-01';"

-- 전 기간 통합 (주의: 대용량)
psql-tango -c "
SELECT * FROM opark_daily_report WHERE created_at >= '2026-03-01'
UNION ALL
SELECT * FROM opark_daily_archive WHERE archived_at >= '2026-03-01'
LIMIT 100;
"
```

**주의**: archive 테이블은 184만 건으로 대용량입니다. 반드시 `WHERE` 조건 + `LIMIT`를 사용하세요.

## 분석 결과 저장

```bash
# 엑셀로 저장 (Python)
python3 -c "
import pandas as pd
import subprocess
result = subprocess.run(['psql-tango', '-c', 'COPY (SELECT ...) TO STDOUT WITH CSV HEADER'], capture_output=True, text=True)
# 또는 직접 pandas로 처리
"

# 차트 생성
python3 -c "
import matplotlib.pyplot as plt
# ... 차트 코드
plt.savefig('/home/node/workspace/exports/chart.png')
"
```

파일은 `~/workspace/exports/` 또는 `~/workspace/reports/`에 저장하면 브라우저 /files/ 페이지에서 다운로드 가능합니다.
