FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir redis>=5.0 sqlalchemy>=2.0 psycopg2-binary>=2.9

COPY worker.py .

CMD ["python", "-u", "worker.py"]
