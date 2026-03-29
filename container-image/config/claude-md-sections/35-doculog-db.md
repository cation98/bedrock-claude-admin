
### 4. Docu-Log 문서활동 분석 DB

문서활동 로그 267일간 4,616,363건 분석 결과. 부서별 업무 현황, 업무 중복, 반복 패턴 질의 가능.

```bash
# 올바른 방법
psql-doculog -c "쿼리"
```

**주요 테이블**:

| 테이블 | 설명 | 건수 |
|--------|------|------|
| `document_logs` | 문서활동 로그 원본 + 분석 컬럼 | 4,616,363 |
| `task_embeddings` | 업무명 임베딩 벡터 (768dim) | 359,968 |
| `mv_pre_reorg` | 2025년 개편 전 데이터 뷰 | 4,037,324 |

**핵심 컬럼**:

| 컬럼 | 설명 |
|------|------|
| `fn_task_normalized` | 핵심 분석 단위 — 날짜/버전 제거된 업무명 |
| `fn_doc_type` | 문서 유형 (현황/보고서/점검/계획 등 13종) |
| `department` | 소속 부서 (192개) |
| `dept_function` | 부서 기능 (품질혁신, Access관제 등) |
| `dept_region` | 부서 지역 (서울, 경남 등 18개) |
| `log_type` | 활동 유형 (편집, 생성, 읽기 등) |

**자주 쓰는 쿼리**:
```sql
-- 부서기능별 주요 업무 Top 10
psql-doculog -c "SELECT fn_task_normalized, COUNT(*) FROM document_logs WHERE dept_function = '품질혁신' GROUP BY 1 ORDER BY 2 DESC LIMIT 10;"

-- 부서간 업무 중복
psql-doculog -c "SELECT dept_function, COUNT(DISTINCT fn_task_normalized) FROM document_logs GROUP BY 1 ORDER BY 2 DESC;"

-- 유사 업무 시맨틱 검색
psql-doculog -c "SET hnsw.ef_search = 100; SELECT e2.fn_task_normalized, ROUND((1-(e1.embedding<=>e2.embedding))::numeric,4) AS sim FROM task_embeddings e1 CROSS JOIN LATERAL (SELECT fn_task_normalized,embedding FROM task_embeddings WHERE fn_task_normalized!=e1.fn_task_normalized ORDER BY embedding<=>e1.embedding LIMIT 5) e2 WHERE e1.fn_task_normalized='안전점검';"
```
