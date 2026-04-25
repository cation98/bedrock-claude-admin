FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "redis>=5.0" \
    "sqlalchemy>=2.0" \
    "psycopg2-binary>=2.9" \
    "prometheus_client>=0.20.0" \
    "httpx>=0.27"

COPY worker.py .
COPY scripts/reconcile-aws-vs-db.py /app/scripts/reconcile-aws-vs-db.py

CMD ["python", "-u", "worker.py"]
