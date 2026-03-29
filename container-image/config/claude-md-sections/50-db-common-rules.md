
### DB 공통 규칙
1. **TANGO DB → `psql-tango -c "쿼리"`** (절대 `$TANGO_DATABASE_URL` 직접 사용 금지)
2. **Safety DB → `psql $DATABASE_URL -c "쿼리"`**
3. **Docu-Log DB → `psql-doculog -c "쿼리"`**
4. 대량 데이터 → `LIMIT` 사용
5. 한글 데이터 포함
