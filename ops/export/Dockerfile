# Phase 1a ops/export CI image
#
# 목적: ISMS-P 제35조 데이터 주체 권리 대응 도구 컨테이너화
# Build: docker build --platform linux/amd64 \
#          -t $ECR/bedrock-claude/ops-export:latest \
#          -f ops/export/Dockerfile .
# Push:  aws ecr get-login-password --region ap-northeast-2 | \
#          docker login --username AWS --password-stdin $ECR && \
#          docker push $ECR/bedrock-claude/ops-export:latest
#
# Run 예시:
#   docker run --rm -e DATABASE_URL="postgresql://..." \
#     ops-export ops.export.chats --user TESTUSER01 --since 90
#
# EKS Job 실행 예시는 ops/export/README.md 참조.

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# psycopg2-binary는 prebuilt wheel 제공 — apt 패키지 불요
# pyarrow도 manylinux wheel로 설치됨

COPY ops/export/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ops/export /app/ops/export

# Non-root user (ISMS-P 최소권한)
RUN addgroup --system --gid 1000 opsexport && \
    adduser --system --uid 1000 --gid 1000 opsexport && \
    chown -R opsexport:opsexport /app
USER opsexport

ENTRYPOINT ["python", "-m"]
CMD ["ops.export.chats", "--help"]
