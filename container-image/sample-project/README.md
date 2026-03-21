# Safety Management Sample - Claude Code 실습

이 프로젝트는 Claude Code 실습을 위한 샘플 FastAPI 애플리케이션입니다.
Safety Management 시스템의 간소화된 버전으로, RDS ReadOnly Replica에 접속하여
실제 데이터를 조회하고 분석하는 실습을 할 수 있습니다.

## 실습 가이드

### 1. Claude Code 시작
```bash
claude
```

### 2. 실습 예시

Claude Code에 다음과 같은 요청을 해보세요:

- "이 프로젝트의 구조를 설명해줘"
- "DB에 어떤 테이블이 있는지 확인해줘"
- "사용자 목록을 조회하는 API 엔드포인트를 만들어줘"
- "이 코드에서 개선할 점을 찾아줘"
- "테스트 코드를 작성해줘"

### 3. DB 접속 (ReadOnly)
```bash
psql $DATABASE_URL
```

## 프로젝트 구조
```
sample-project/
├── app/
│   ├── main.py          # FastAPI 앱 진입점
│   ├── config.py        # 설정 관리
│   ├── models.py        # SQLAlchemy 모델
│   ├── schemas.py       # Pydantic 스키마
│   └── routes/
│       └── safety.py    # 안전관리 API
├── requirements.txt
└── README.md
```
