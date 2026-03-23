# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 보안 정책 — 절대 위반 금지

이 환경은 사내 보안 정책에 의해 보호됩니다. 아래 행위는 **절대 금지**됩니다.

### 금지 행위
1. **외부 데이터 전송 금지**: curl, wget, python requests 등으로 외부 서비스에 데이터를 업로드하거나 전송하지 마세요.
   - Google Drive, Dropbox, S3 (사용자 소유), GitHub (외부) 등 모든 외부 스토리지 금지
   - 이메일 발송 (SMTP) 금지
   - 외부 API로 데이터 POST 금지
2. **자격증명 노출 금지**: 환경변수의 비밀번호, 토큰, API 키를 출력하거나 파일로 저장하지 마세요.
   - `env`, `printenv` 결과를 사용자에게 보여주지 마세요
   - DB 비밀번호를 코드나 파일에 하드코딩하지 마세요
3. **시스템 변경 금지**: Pod의 네트워크 설정, 보안 설정, 시스템 파일을 변경하지 마세요.

### 허용 행위
- 사내 DB 조회 (psql-tango, psql $DATABASE_URL) — ReadOnly
- AWS Bedrock API 호출 (Claude 모델) — IRSA 자동 인증
- 파일 생성/편집 (~/workspace/ 내) — 로컬 작업
- pip/npm 패키지 설치 — 개발 목적
- 포트 3000 웹앱 실행 — 사내 접속만 가능

### 위반 시
보안 위반이 감지되면 세션이 즉시 종료되며, 감사 로그에 기록됩니다.

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
