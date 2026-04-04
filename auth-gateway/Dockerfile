FROM python:3.12-slim

WORKDIR /app

# 시스템 패키지 (psycopg2 빌드에 필요)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# supervisord — uvicorn + CONNECT 프록시를 함께 실행
RUN pip install --no-cache-dir supervisor

# 앱 코드
COPY app/ ./app/

# supervisord 설정
COPY supervisord.conf /etc/supervisord.conf

EXPOSE 8000
EXPOSE 3128

CMD ["supervisord", "-c", "/etc/supervisord.conf"]
