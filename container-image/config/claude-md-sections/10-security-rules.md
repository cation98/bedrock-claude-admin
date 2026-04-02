
## 보안 정책 (자동 생성)

- 보안 등급: {SECURITY_LEVEL}
- 허용되지 않은 데이터: "접근 권한이 없습니다"로 응답
- DB 구조/테이블명/컬럼명을 허용 범위 밖에서 노출 금지
- env, printenv, .pgpass 파일 내용 절대 노출 금지
- `.efs-users/` 디렉토리에 직접 파일 복사/쓰기 금지 (Read-only). 파일 공유는 반드시 `/share` 스킬의 API를 사용

## 대용량 파일 처리 — 절대 위반 금지

**Excel/CSV 파일을 분석할 때 아래 규칙을 반드시 따르세요. 예외 없음.**

1. **파일 2개 이상 동시 분석** → `pd.read_excel()` 직접 사용 금지. 반드시 SQLite에 병합 후 SQL로 분석
2. **단일 파일이라도 5MB 초과** → SQLite 변환 후 SQL로 분석 권장
3. **같은 파일을 2번 이상 `pd.read_excel()`로 읽지 마세요** — 한 번 읽어서 SQLite 저장 후 SQL로만
4. **기존 SQLite가 있는지 먼저 확인**: `ls ~/workspace/shared-data/*.sqlite` → 있으면 그것을 사용

**올바른 절차:**
```python
# ❌ 절대 금지 — 여러 파일을 pandas로 직접 로딩
df1 = pd.read_excel("TBM_1월.xlsx")  # 메모리 ~800MB
df2 = pd.read_excel("TBM_2월.xlsx")  # 메모리 ~800MB 추가

# ✅ 올바른 방법 — SQLite에 병합 후 SQL
import pandas as pd, sqlite3
conn = sqlite3.connect("~/workspace/shared-data/tbm.sqlite")
pd.read_excel("TBM_1월.xlsx").to_sql("jan", conn, if_exists="replace", index=False)
pd.read_excel("TBM_2월.xlsx").to_sql("feb", conn, if_exists="replace", index=False)
conn.close()
# 이후는 SQL로만 분석
# sqlite3 ~/workspace/shared-data/tbm.sqlite "SELECT ..."
```
