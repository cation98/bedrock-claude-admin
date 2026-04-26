# =============================================================================
# App Runtime Container Image
# 사용자가 배포한 웹앱을 실행하는 경량 런타임 컨테이너
# claude-apps namespace에서 실행되며, 개인 Pod(claude-sessions)과 분리
#
# 지원 앱 유형:
#   - Python (FastAPI/uvicorn) -- main.py 또는 app.py 자동 감지
#   - Node.js (npm start / server.js / index.js)
#   - 커스텀 (start.sh)
# =============================================================================

FROM node:22-bookworm-slim

# ---------------------------------------------------------------------------
# 시스템 패키지 설치
#   python3, pip, venv  -- Python 앱 실행용
#   curl                -- 헬스체크 및 외부 API 호출용
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python 패키지 사전 설치
#   FastAPI 웹앱 + 데이터 분석에 필요한 핵심 패키지를 미리 설치
#   사용자 앱의 requirements.txt에 동일 패키지가 있으면 캐시 히트
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir --break-system-packages \
    fastapi \
    uvicorn[standard] \
    pandas \
    openpyxl \
    psycopg2-binary \
    jinja2 \
    python-multipart \
    matplotlib

# ---------------------------------------------------------------------------
# 앱 디렉토리 설정
#   /app -- EFS에서 마운트되는 앱 코드 (deployed/{app}/current/ -> /app)
#   EXPOSE 3000 -- Ingress가 이 포트로 트래픽 라우팅
# ---------------------------------------------------------------------------
WORKDIR /app

EXPOSE 3000

# ---------------------------------------------------------------------------
# 보안 미들웨어 내장 — 앱에서 from security_middleware import add_security
# OWASP Top 10 대응: rate limiting, security headers, upload validation
# ---------------------------------------------------------------------------
COPY security_middleware.py /usr/local/lib/python3.11/dist-packages/security_middleware.py

# ---------------------------------------------------------------------------
# psql 래퍼 스크립트 — subprocess로 DB 접근하는 앱을 위한 CLI 툴
#   psql-tango : TANGO 알람 DB ($TANGO_DATABASE_URL)
#   psql-safety: Safety 안전관리 DB ($DATABASE_URL)
# URL 파싱으로 특수문자 패스워드를 PGPASSWORD로 분리하여 인증 실패 방지
# ---------------------------------------------------------------------------
COPY scripts/psql-tango /usr/local/bin/psql-tango
COPY scripts/psql-safety /usr/local/bin/psql-safety
RUN chmod +x /usr/local/bin/psql-tango /usr/local/bin/psql-safety

# ---------------------------------------------------------------------------
# 엔트리포인트 -- 앱 타입 자동 감지 후 실행
# ---------------------------------------------------------------------------
COPY app-entrypoint.sh /usr/local/bin/app-entrypoint.sh
RUN chmod +x /usr/local/bin/app-entrypoint.sh

CMD ["app-entrypoint.sh"]
