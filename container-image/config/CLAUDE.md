# Claude Code 사내 플랫폼 — 글로벌 설정

이 터미널은 SKO 사내 Claude Code 플랫폼입니다. AWS Bedrock 기반, 사내망 내에서 동작합니다.

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

## 사용 가능한 도구

- **psql-tango**: TANGO 알람 DB 접속
- **psql $DATABASE_URL**: 안전관리 DB 접속
- **Python 3**: pandas, matplotlib, openpyxl 설치됨
- **git**: 버전 관리
- **AWS CLI**: AWS 리소스 조회

## 파일 관리

- 업로드된 파일: `~/workspace/uploads/`
- 보고서 저장: `~/workspace/reports/`
- 엑셀 저장: `~/workspace/exports/`
- 파일 다운로드: 브라우저 /files/ 페이지
