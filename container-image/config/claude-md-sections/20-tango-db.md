
## DB 접속 방법 — 반드시 준수

### 1. TANGO 알람 DB (네트워크 고장 실시간)

**반드시 `psql-tango` 명령어를 사용하세요.**

```bash
# 올바른 방법 — 항상 이것만 사용
psql-tango -c "쿼리"

# 절대 사용 금지 (패스워드 특수문자로 인증 실패)
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
