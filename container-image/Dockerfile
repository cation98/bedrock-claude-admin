# =============================================================================
# Claude Code Terminal Image
# 사용자별 격리 환경: Claude Code CLI + ttyd 웹 터미널 + DB 클라이언트
# =============================================================================

FROM node:22-bookworm-slim AS base

# 빌드 인자
ARG TTYD_VERSION=1.7.7
ARG AWSCLI_VERSION=2.22.35

# 시스템 패키지 설치
# - ttyd 빌드 의존성 + 런타임 도구
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 기본 도구
    curl \
    wget \
    unzip \
    git \
    vim-tiny \
    less \
    jq \
    ca-certificates \
    # Python (Claude Code 일부 기능 + 실습용)
    python3 \
    python3-pip \
    python3-venv \
    # PostgreSQL 클라이언트 (RDS 접속용)
    postgresql-client \
    # ttyd 빌드 의존성
    build-essential \
    cmake \
    libjson-c-dev \
    libwebsockets-dev \
    # 프로세스 관리
    tini \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# ttyd 설치 (웹 터미널 서버)
# GitHub Release에서 pre-built binary 다운로드
# ttyd: 브라우저에서 터미널을 사용할 수 있게 해주는 도구
# https://github.com/tsl0922/ttyd
# -----------------------------------------------------------------------------
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then TTYD_ARCH="x86_64"; \
    elif [ "$ARCH" = "arm64" ]; then TTYD_ARCH="aarch64"; \
    else echo "Unsupported arch: $ARCH" && exit 1; fi && \
    curl -fsSL "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.${TTYD_ARCH}" \
      -o /usr/local/bin/ttyd && \
    chmod +x /usr/local/bin/ttyd

# -----------------------------------------------------------------------------
# AWS CLI v2 설치
# Pod에서 AWS 리소스 접근 시 사용 (STS 자격증명 확인 등)
# -----------------------------------------------------------------------------
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then AWS_ARCH="x86_64"; \
    elif [ "$ARCH" = "arm64" ]; then AWS_ARCH="aarch64"; \
    else echo "Unsupported arch: $ARCH" && exit 1; fi && \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip" -o /tmp/awscliv2.zip && \
    unzip -q /tmp/awscliv2.zip -d /tmp && \
    /tmp/aws/install && \
    rm -rf /tmp/aws /tmp/awscliv2.zip

# -----------------------------------------------------------------------------
# Claude Code CLI 설치
# Anthropic의 공식 AI 코딩 어시스턴트 CLI
# CLAUDE_CODE_USE_BEDROCK=1 환경변수로 AWS Bedrock 백엔드 사용
# -----------------------------------------------------------------------------
RUN npm install -g @anthropic-ai/claude-code && \
    npm cache clean --force

# 빌드 의존성 정리 (이미지 크기 축소)
RUN apt-get purge -y build-essential cmake && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 실습 사용자 설정
# node:22 이미지에는 UID 1000의 'node' 사용자가 이미 존재
# 이 사용자의 홈 디렉토리와 셸을 설정하여 재활용
# -----------------------------------------------------------------------------
RUN usermod -s /bin/bash node && \
    mkdir -p /home/node/workspace /home/node/.claude && \
    chown -R node:node /home/node

# -----------------------------------------------------------------------------
# 샘플 프로젝트 복사 (실습용)
# -----------------------------------------------------------------------------
COPY --chown=node:node sample-project/ /home/node/workspace/sample-project/
COPY --chown=node:node sample-project/CLAUDE.md /home/node/CLAUDE.md

# Python 패키지 사전 설치 (엑셀 생성, 데이터 분석)
RUN pip3 install --no-cache-dir --break-system-packages openpyxl pandas matplotlib

# -----------------------------------------------------------------------------
# Claude Code 설정 + Skills
# -----------------------------------------------------------------------------
COPY --chown=node:node config/settings.json /home/node/.claude/settings.json
COPY --chown=node:node config/skills/ /home/node/.claude/commands/

# 엔트리포인트 스크립트
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 작업 디렉토리 설정 (홈 디렉토리에서 시작)
WORKDIR /home/node
USER node

# node 사용자로 전환 후 git init + Claude 설정
RUN cd /home/node/workspace/sample-project && \
    git init && \
    git config user.name "Workshop" && \
    git config user.email "workshop@skons.net" && \
    git add -A && \
    git commit -m "Initial sample project" --quiet

# ttyd 웹 터미널 포트
EXPOSE 7681

# 환경변수 기본값 (런타임에 오버라이드)
ENV CLAUDE_CODE_USE_BEDROCK=1 \
    AWS_REGION=us-east-1 \
    TERM=xterm-256color \
    LANG=C.UTF-8

# tini: PID 1 프로세스 매니저 (좀비 프로세스 방지, 시그널 전달)
ENTRYPOINT ["tini", "--"]
CMD ["/usr/local/bin/entrypoint.sh"]
