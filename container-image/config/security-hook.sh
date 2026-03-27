#!/bin/bash
# PreToolUse hook: 위험 명령 실시간 차단
# Claude Code가 Bash 도구 실행 전 이 스크립트를 호출
# exit 0 = 허용, exit 2 = 차단
INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null)

if [ "$TOOL" = "Bash" ]; then
    CMD=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))" 2>/dev/null)

    # 자격증명 노출 차단
    if echo "$CMD" | grep -qiE '^(env|printenv|set)$'; then
        echo "BLOCKED: credential exposure" >&2
        exit 2
    fi
    if echo "$CMD" | grep -qiE '\.pgpass|\.env|credential|password|secret|auth-gateway-secrets'; then
        echo "BLOCKED: credential file access" >&2
        exit 2
    fi
    # /proc 환경변수 접근 차단
    if echo "$CMD" | grep -qiE '/proc/.*/environ'; then
        echo "BLOCKED: process environment access" >&2
        exit 2
    fi
    # K8s API/Secret 접근 차단
    if echo "$CMD" | grep -qiE '/var/run/secrets|kubernetes\.default|kubectl'; then
        echo "BLOCKED: K8s API access" >&2
        exit 2
    fi
    # 외부 데이터 전송 차단 (curl/wget은 settings.json deny에서도 차단하지만 이중 방어)
    if echo "$CMD" | grep -qiE 'curl\s+.*(-d|--data|--upload)|wget\s+.*--post|nc\s|ncat\s|socat\s'; then
        echo "BLOCKED: outbound data transfer" >&2
        exit 2
    fi
fi

exit 0
