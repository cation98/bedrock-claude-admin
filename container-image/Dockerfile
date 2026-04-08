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

# Python 패키지 사전 설치 (데이터 분석 + 웹앱 개발)
RUN pip3 install --no-cache-dir --break-system-packages \
    openpyxl pandas matplotlib \
    fastapi uvicorn[standard] psycopg2-binary jinja2 python-multipart \
    xlrd python-docx python-pptx qrcode reportlab fpdf2 \
    httpx requests markdown duckdb \
    uv

# Serena MCP 서버 사전 설치 (uvx로 실행)
RUN pip3 install --no-cache-dir --break-system-packages serena || true

# -----------------------------------------------------------------------------
# Claude Code 설정 + Skills
# -----------------------------------------------------------------------------
COPY --chown=node:node config/settings.json /home/node/.claude/settings.json
# CLAUDE.md sections for dynamic assembly (entrypoint.sh assembles at runtime)
COPY --chown=node:node config/claude-md-sections/ /home/node/.claude/claude-md-sections/
# Minimal fallback (overwritten by entrypoint)
COPY --chown=node:node config/CLAUDE.md.minimal /home/node/.claude/CLAUDE.md

# Security hook: PreToolUse에서 위험 명령 차단
RUN mkdir -p /home/node/.claude/hooks
COPY --chown=node:node config/security-hook.sh /home/node/.claude/hooks/security-hook.sh
RUN chmod 555 /home/node/.claude/hooks/security-hook.sh

# NOTE: chmod 444는 entrypoint.sh 마지막에 설정 (entrypoint에서 CLAUDE.md/settings.json 동적 수정 후)
COPY --chown=node:node config/skills/ /home/node/.claude/commands/
COPY --chown=node:node config/claude.json /home/node/.claude.json

# Superpowers 플러그인 (스킬 + 에이전트)
COPY --chown=node:node config/plugins/superpowers/ /home/node/.claude/plugins/cache/superpowers-marketplace/superpowers/4.0.3/
COPY --chown=node:node config/plugins/serena/ /home/node/.claude/plugins/cache/claude-plugins-official/serena/unknown/
COPY --chown=node:node config/installed_plugins.json /home/node/.claude/plugins/installed_plugins.json
COPY --chown=node:node config/plugins-config.json /home/node/.claude/plugins/config.json
COPY --chown=node:node config/plugins-blocklist.json /home/node/.claude/plugins/blocklist.json
COPY --chown=node:node config/plugins-known-marketplaces.json /home/node/.claude/plugins/known_marketplaces.json
COPY --chown=node:node config/plugins-marketplaces/ /home/node/.claude/plugins/marketplaces/
COPY --chown=node:node config/plugins-data/ /home/node/.claude/plugins/data/

# Tabulator.js (파일 탐색기 UI) — CDN 불가 환경 대비 로컬 번들
RUN mkdir -p /opt/static && \
    curl -fsSL "https://unpkg.com/tabulator-tables@6.3.0/dist/js/tabulator.min.js" \
      -o /opt/static/tabulator.min.js && \
    curl -fsSL "https://unpkg.com/tabulator-tables@6.3.0/dist/css/tabulator_midnight.min.css" \
      -o /opt/static/tabulator_midnight.min.css

# 파일 업로드/다운로드 서버
COPY fileserver.py /usr/local/bin/fileserver.py

# 웹앱 배포 CLI 스크립트 (entrypoint.sh가 /home/node/.local/bin/에 설치)
COPY scripts/deploy.sh /usr/local/bin/deploy.sh
RUN chmod +x /usr/local/bin/deploy.sh

# 공유 데이터 심링크 동기화 스크립트 (entrypoint.sh가 백그라운드로 실행)
COPY scripts/share-sync.sh /usr/local/bin/share-sync.sh
RUN chmod +x /usr/local/bin/share-sync.sh

# ttyd 커스텀 index.html — Ctrl+C/V 클립보드 + 이미지 붙여넣기
RUN mkdir -p /usr/local/share/ttyd
COPY ttyd-custom-index.html /usr/local/share/ttyd/index.html

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

# 포트: 7681(ttyd), 8080(파일서버), 3000(사용자 웹앱)
EXPOSE 7681 8080 3000

# 환경변수 기본값 (런타임에 오버라이드)
ENV CLAUDE_CODE_USE_BEDROCK=1 \
    AWS_REGION=us-east-1 \
    TERM=xterm-256color \
    LANG=C.UTF-8

# tini: PID 1 프로세스 매니저 (좀비 프로세스 방지, 시그널 전달)
ENTRYPOINT ["tini", "--"]
CMD ["/usr/local/bin/entrypoint.sh"]
