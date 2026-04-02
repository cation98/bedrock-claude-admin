
### 3. Opark 업무일지 DB (TANGO DB와 동일 서버)

Opark 테이블은 TANGO와 같은 DB(`postgres`)에 있으므로 `psql-tango`로 접근합니다.

```bash
psql-tango -c "SELECT * FROM opark_daily_report LIMIT 5;"
```

**중요: 실시간 vs 과거 데이터 구분**

| 테이블 | 건수 | 용도 | 데이터 범위 |
|--------|------|------|------------|
| `opark_daily_report` | ~183K | **실시간** 업무일지 (1분 주기 upsert) | 최근 데이터 |
| `opark_daily_archive` | ~1.8M | **과거** 업무일지 아카이브 | 이전 기간 전체 |

**사용자가 업무일지/Opark 데이터를 요청하면 반드시 기간을 확인하세요:**
```
사용자: "3월 업무일지 분석해줘"
Claude: "어느 기간의 데이터인가요?
  - 최근 데이터(opark_daily_report): 현재 실시간 반영 중
  - 과거 데이터(opark_daily_archive): 아카이브된 이력 데이터
  조회할 기간(예: 2026-03-01 ~ 2026-03-31)을 알려주세요."
```

**기간별 쿼리 방법:**
```sql
-- 실시간 데이터 (최근)
psql-tango -c "SELECT * FROM opark_daily_report WHERE created_at >= '2026-04-01' LIMIT 10;"

-- 과거 아카이브 데이터
psql-tango -c "SELECT * FROM opark_daily_archive WHERE archived_at >= '2026-03-01' AND archived_at < '2026-04-01' LIMIT 10;"

-- 전 기간 통합 조회 (UNION)
psql-tango -c "
SELECT * FROM opark_daily_report WHERE created_at >= '2026-03-01'
UNION ALL
SELECT * FROM opark_daily_archive WHERE archived_at >= '2026-03-01' AND archived_at < '2026-04-01'
LIMIT 100;
"
```

**Opark 테이블**:

| 테이블 | 설명 |
|--------|------|
| `opark_daily_report` | 실시간 업무일지 (1분 주기 upsert) |
| `opark_daily_archive` | **과거 업무일지 아카이브** (과년도/이전 기간) |
| `report_embeddings` | pgvector 768dim 벡터 (ko-sroberta) |
| `report_ontology` | 5단계 업무 분류 트리 (level/code/parent_code) |
| `report_alarm_matches` | 알람-업무 유사도 매칭 결과 |
| `opark_b2bequipmaster` | B2B 장비 마스터 |
| `opark_cmsequipmaster` | CMS 장비 마스터 |
| `opark_equipmaster` | 장비 마스터 |
| `opark_evchrgequipmaster` | 전기차 충전 장비 |
| `opark_fronthaulequipmaster` | Fronthaul 장비 |
