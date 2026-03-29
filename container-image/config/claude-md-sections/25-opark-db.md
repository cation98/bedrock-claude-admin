
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
