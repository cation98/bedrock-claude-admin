
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
- pandas.read_excel(): 10MB 이하 **단일 파일**만 허용
- **여러 파일 동시 분석**: 합산 크기 무관하게 **반드시 SQLite에 병합 후 SQL 분석** (`/db` 스킬 참조)
- 같은 Excel을 **2번 이상 pd.read_excel()로 읽지 마세요** — 한 번 읽어서 SQLite 저장 후 SQL로만
- 대용량 데이터 분석: SQL 쿼리 우선, pandas는 결과만 로딩

### 메모리 경고 대응 (OOMKilled 방지)

이 Pod의 메모리는 제한되어 있습니다. 초과 시 **Pod가 즉시 강제 종료**되어 모든 작업이 중단됩니다.

**작업 전 반드시 메모리 확인:**
```bash
# 현재 메모리 사용량 확인
cat /sys/fs/cgroup/memory.current | awk '{printf "%.0f MB\n", $1/1048576}'
cat /sys/fs/cgroup/memory.max | awk '{printf "%.0f MB (한도)\n", $1/1048576}'
```

**`/tmp/.memory-warning` 파일이 존재하면 즉시 대응:**
1. 현재 실행 중인 대용량 작업을 **즉시 중단**
2. `del df` 등으로 큰 DataFrame 변수를 해제
3. `gc.collect()`로 가비지 컬렉션 실행
4. 데이터를 SQLite로 저장하고 메모리에서 제거 후 SQL로 분석

**절대 금지:**
- 100MB 초과 파일을 pandas로 한 번에 로딩
- 여러 대용량 DataFrame을 동시에 메모리에 유지
- `pd.concat()`으로 대용량 데이터 병합 (SQLite INSERT로 대체)
