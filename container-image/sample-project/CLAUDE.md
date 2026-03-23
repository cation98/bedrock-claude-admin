# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 환경 정보

이 터미널은 SKO 사내 Claude Code 플랫폼입니다. AWS Bedrock 기반으로 사내망 내에서 동작합니다.

## Language
Always respond in Korean.

## DB 접속 — 반드시 이 방법을 사용할 것

### 1. TANGO 알람 DB (네트워크 고장 실시간 데이터)

**반드시 `psql-tango` 명령어를 사용하세요.** `$TANGO_DATABASE_URL` 환경변수는 직접 사용하지 마세요 (패스워드 특수문자 이슈).

```bash
# 올바른 방법 (항상 이것을 사용)
psql-tango -c "SELECT OP_TEAM_ORG_NM, COUNT(*) FROM alarm_data GROUP BY OP_TEAM_ORG_NM ORDER BY COUNT(*) DESC;"

# 인터랙티브 접속
psql-tango
```

**주요 테이블**:
| 테이블 | 설명 | 보존기간 |
|--------|------|---------|
| `alarm_data` | 현재 활성 고장 | 7일 |
| `alarm_events` | 전체 이벤트 로그 | 30일 |
| `alarm_history` | 복구된 고장 이력 | — |
| `facility_info` | 장비 마스터 (JSONB) | 영구 |
| `alarm_hourly_summary` | 시간대별 집계 | — |

**뷰**: `alarm_statistics` — 운용팀별 현황 요약 (team_name, alarm_count, new_alarms, unacked_alarms, locked_alarms, latest_alarm)

**알람 상태값**:
- 활성: `O`(발생), `U`(미확인), `L`(잠금)
- 해제: `C`(복구), `F`(사용자복구), `A`(인지), `D`(삭제)

**주요 컬럼**: `EQP_NM`(장비명), `FALT_OCCR_LOC_CTT`(고장위치), `OP_TEAM_ORG_NM`(운용팀), `OP_HDOFC_ORG_NM`(본부), `EVT_TIME`(발생시각), `ALM_STAT_VAL`(상태), `ALM_DESC`(설명), `MCP_NM`(시도), `SGG_NM`(시군구), `EQP_ID`(장비ID)

### 2. 안전관리 DB (Safety)

```bash
# 올바른 방법
psql $DATABASE_URL -c "쿼리"

# 인터랙티브 접속
psql $DATABASE_URL
```

- 읽기 전용 (SELECT만 가능)
- DB: safety
- 테이블 목록: `psql $DATABASE_URL -c "\dt"`

### DB 조회 시 필수 규칙

1. **TANGO DB는 반드시 `psql-tango -c "쿼리"` 사용** — `$TANGO_DATABASE_URL` 직접 사용 금지
2. **Safety DB는 `psql $DATABASE_URL -c "쿼리"` 사용**
3. 대량 데이터 조회 시 `LIMIT` 사용
4. 한글 데이터가 포함되어 있음

## 사용 가능한 도구

- **psql-tango**: TANGO 알람 DB 접속 (위 설명 참조)
- **psql $DATABASE_URL**: 안전관리 DB 접속
- **Python 3**: pandas, matplotlib, openpyxl 사전 설치됨
- **git**: 버전 관리
- **AWS CLI**: AWS 리소스 조회

## 파일 관리

- **업로드된 파일**: `~/workspace/uploads/`
- **보고서 저장**: `~/workspace/reports/`
- **엑셀 저장**: `~/workspace/exports/`
- 파일 다운로드: 브라우저에서 /files/ 페이지 사용

## 프로젝트 구조
```
sample-project/
├── app/
│   ├── main.py          # FastAPI 앱
│   ├── config.py        # 설정
│   ├── models.py        # SQLAlchemy 모델
│   ├── schemas.py       # Pydantic 스키마
│   └── routes/
│       └── safety.py    # 안전관리 API
└── requirements.txt
```
