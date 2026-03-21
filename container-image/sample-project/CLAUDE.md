# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 환경 정보

이 터미널은 SKO 사내 Claude Code 실습 환경입니다.

### 사용 가능한 도구
- **psql**: `psql $DATABASE_URL` 로 safety-prod DB (ReadOnly Replica) 접속
- **Python 3**: 데이터 분석, 시각화
- **git**: 버전 관리
- **AWS CLI**: AWS 리소스 조회

### DB 접속
- 환경변수 `$DATABASE_URL`에 ReadOnly Replica 접속 정보가 설정되어 있음
- 읽기 전용 (SELECT만 가능, INSERT/UPDATE/DELETE 불가)
- DB 이름: safety
- 주요 테이블은 `psql $DATABASE_URL -c "\dt"` 로 확인

### DB 조회 시 규칙
- 항상 `psql $DATABASE_URL -c "쿼리"` 형식으로 실행
- 대량 데이터 조회 시 `LIMIT` 사용
- 한글 컬럼명이 있을 수 있으므로 큰따옴표 사용

### 프로젝트 구조
```
sample-project/
├── app/
│   ├── main.py          # FastAPI 앱
│   ├── config.py        # 설정
│   ├── models.py        # SQLAlchemy 모델
│   ├── schemas.py       # Pydantic 스키마
│   └── routes/
│       └── safety.py    # 안전관리 API
└── requirements.txt
```

## Language
Always respond in Korean.
