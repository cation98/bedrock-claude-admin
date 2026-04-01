
## 대용량 파일 처리 규칙

### 데이터베이스 자동 발견
10MB 초과 Excel/CSV 업로드 시 SQLite + 스키마 파일이 자동 생성됩니다:
- `~/workspace/shared-data/{name}.sqlite` — 데이터
- `~/workspace/shared-data/{name}.schema.md` — 테이블/컬럼/샘플 정보

**데이터 분석 요청 시 반드시 스키마 파일을 먼저 확인하세요:**
```bash
cat ~/workspace/shared-data/*.schema.md   # 내 데이터 스키마
cat ~/workspace/team/*/*.schema.md        # 팀 공유 데이터 스키마
```

### 업로드된 파일 우선순위
1. `.schema.md` 파일로 DB 구조 확인
2. `.sqlite` 파일이 있으면 **항상** SQLite를 사용 (pandas 직접 로딩 금지)
3. SQLite 쿼리: `sqlite3 ~/workspace/shared-data/{name}.sqlite "SELECT ..."`

### 팀 공유 데이터 접근
- 내 데이터: `~/workspace/shared-data/` (읽기+쓰기)
- 팀 데이터: `~/workspace/team/{소유자사번}/` (읽기 전용)
- 팀 데이터에 쓰기가 필요하면 웹앱을 통해 처리하세요

### 메모리 보호 규칙
- 50MB 초과 파일: 반드시 SQLite 또는 청크 처리
- pandas.read_excel(): 10MB 이하만 허용
- 대용량 데이터 분석: SQL 쿼리 우선, pandas는 결과만 로딩
