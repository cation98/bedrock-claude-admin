
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
