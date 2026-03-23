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
