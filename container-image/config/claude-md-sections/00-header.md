# Claude Code 사내 플랫폼 — 글로벌 설정

이 터미널은 SKO 사내 Claude Code 플랫폼입니다. AWS Bedrock 기반, 사내망 내에서 동작합니다.

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
- 사내 DB 조회 (psql-tango, psql-doculog, psql $DATABASE_URL) — ReadOnly
- AWS Bedrock API 호출 (Claude 모델) — IRSA 자동 인증
- 파일 생성/편집 (~/workspace/ 내) — 로컬 작업
- pip/npm 패키지 설치 — 개발 목적
- 포트 3000 웹앱 실행 — 사내 접속만 가능

### 위반 시
보안 위반이 감지되면 세션이 즉시 종료되며, 감사 로그에 기록됩니다.

## Language
Always respond in Korean.
