
## 웹 터미널 조작 안내

이 환경은 웹 브라우저 기반 터미널(ttyd)입니다.
- **슬래시 명령(`/`) 목록 탐색**: `j`(아래), `k`(위) 키 사용 (화살표 키는 웹 터미널에서 동작하지 않을 수 있음)
- **선택**: Enter 키

## 사용 가능한 도구

- **psql-tango**: TANGO 알람 DB 접속
- **psql-doculog**: Docu-Log 문서활동 분석 DB 접속
- **psql $DATABASE_URL**: 안전관리 DB 접속
- **Python 3**: pandas, matplotlib, openpyxl 설치됨
- **git**: 버전 관리
- **AWS CLI**: AWS 리소스 조회

## 웹앱 개발 및 접속

Pod에서 웹앱(대시보드, API 등)을 만들면 브라우저에서 접속할 수 있습니다.

### 웹앱 실행 규칙 — 반드시 포트 3000 사용

```bash
# 올바른 방법 — 포트 3000으로 실행
python3 -m uvicorn app:app --host 0.0.0.0 --port 3000

# 다른 포트 사용 금지 (Ingress가 3000만 라우팅)
python3 -m uvicorn app:app --port 8200  # 외부 접속 불가
```

### 접속 URL
웹앱을 포트 3000으로 실행하면 브라우저에서 아래 주소로 접속:
```
https://claude.skons.net/app/{HOSTNAME}/
```
- `{HOSTNAME}`은 Pod 이름 (예: `claude-terminal-n1102359`)
- 환경변수 `$HOSTNAME`으로 확인 가능

### 사전 설치된 패키지 (pip 설치 불필요)
- **fastapi**, **uvicorn** — 웹 프레임워크
- **psycopg2-binary** — PostgreSQL 드라이버
- **jinja2** — HTML 템플릿
- **pandas**, **matplotlib**, **openpyxl** — 데이터 분석/차트/엑셀
- **python-multipart** — 파일 업로드

### 외부 CDN 사용 금지 — 로컬 라이브러리만 사용

배포된 웹앱(App Pod)은 **외부 CDN에 접속할 수 없습니다** (네트워크 정책으로 차단됨).
Chart.js, Bootstrap, jQuery 등 모든 라이브러리는 **반드시 로컬에서 제공**해야 합니다.

```html
<!-- ❌ CDN 사용 금지 (App Pod에서 로딩 불가) -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3/css/bootstrap.min.css">

<!-- ✅ 올바른 방법 1: pip/npm으로 설치 후 로컬 제공 -->
<!-- Python: pip install chart.js는 없으므로 직접 다운로드 -->

<!-- ✅ 올바른 방법 2: 인라인 또는 번들링 -->
<!-- FastAPI StaticFiles로 static/ 디렉토리에서 제공 -->
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="static"), name="static")

<!-- ✅ 올바른 방법 3: 차트는 matplotlib로 서버사이드 생성 -->
import matplotlib.pyplot as plt
plt.savefig("static/chart.png")
<!-- HTML에서 <img src="static/chart.png"> -->

<!-- ✅ 올바른 방법 4: 순수 SVG 차트 (외부 의존성 없음) -->
<!-- SVG <rect>, <line>, <text> 등으로 직접 그리기 -->
```

**차트 라이브러리 대안:**

| CDN 라이브러리 | 로컬 대안 |
|---------------|----------|
| Chart.js | matplotlib (서버 렌더링) 또는 순수 SVG |
| D3.js | 순수 SVG + JavaScript |
| Bootstrap CSS | Tailwind (빌드) 또는 직접 CSS |
| jQuery | 순수 JavaScript (Vanilla JS) |

**개발 중(포트 3000 테스트)에는 CDN 사용 가능**하지만, `deploy`로 배포하면 작동하지 않습니다.
**반드시 배포 전에 모든 외부 CDN 참조를 로컬로 전환하세요.**

### 웹앱 URL 규칙 (무한 리다이렉트 방지)

웹앱은 `/app/{HOSTNAME}/` 경로 뒤에서 실행됩니다. **폼/링크에서 절대 경로(`/`)를 사용하면 로그인 페이지로 이동하는 무한 루프가 발생합니다.**

```html
<!-- 템플릿 <head>에 반드시 추가 -->
<base href="/app/{{ hostname }}/">

<!-- 폼은 상대 경로 사용 -->
<form action="">        ✅
<form action="/">       ❌ (로그인 페이지로 이동)
```

서버에서 `hostname = os.environ.get("HOSTNAME")` 전달 필수.

### 웹앱 예시 (FastAPI 대시보드)
```python
# app.py
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def index():
    return "<h1>대시보드</h1>"

# 실행: python3 -m uvicorn app:app --host 0.0.0.0 --port 3000
# 접속: https://claude.skons.net/app/{HOSTNAME}/
```

## 텔레그램 봇 연동

사내 텔레그램 봇(@SKO_Claude_Bot)이 운영 중입니다.

### 사용 방법
1. 텔레그램에서 @SKO_Claude_Bot 검색 또는 링크 접속
2. `/등록 사번` 입력 (예: `/등록 N1102359`)
3. 자유롭게 질문 (한국어)

### 단체방 사용
- 봇을 단체방에 초대
- `@SKO_Claude_Bot 질문` 형태로 멘션하여 사용
- 개인 DM에서는 멘션 없이 바로 질문 가능

### 텔레그램에서 가능한 것
- DB 조회 결과 분석 (고장 현황, TBM 등)
- 데이터 요약/통계
- SMS 발송 요청
- 일반 질문/번역/요약

### 텔레그램에서 불가능한 것
- 코드 실행 (Pod 터미널 사용)
- 파일 생성/다운로드 (Pod 파일 관리 사용)
- 대시보드 구현 (Pod 웹앱 사용)

## 파일 관리

- 업로드된 파일: `~/workspace/uploads/`
- 보고서 저장: `~/workspace/reports/`
- 엑셀 저장: `~/workspace/exports/`
- 파일 다운로드: 브라우저 /files/ 페이지
