#!/bin/bash
# ask-loop — 답변만 받기 모드 REPL
# 사용자가 질문을 입력하면 최종 답변만 출력하는 반복 루프.
# 각 질문은 독립 세션 — 이전 대화 컨텍스트는 이어지지 않음.
# 슬래시 명령: 내장 명령 + ~/.claude/commands 와 플러그인 skills 자동 탐색.

set -u

CB="\033[1;36m"   # cyan bold
CP="\033[1;32m"   # prompt green
CH="\033[0;90m"   # hint gray
CW="\033[1;33m"   # warning yellow
CE="\033[1;31m"   # error red
CR="\033[0m"      # reset

# ─── Skill 접근 제한 테이블 ────────────────────────────────
# skill_name → 허용 USER_ID 목록 (공백 구분). 빈 값이면 모두 허용.
# 환경변수 $USER_ID 와 대조하여 현재 사용자에게 노출할지 결정.
# allowed-users 를 skill markdown frontmatter 에도 적어둠 — runtime 에서 Claude 가
# 다시 확인하도록 이중 방어.
skill_is_restricted() {
    # 반환: 0 = 제한 (현재 사용자 차단), 1 = 허용
    local skill="$1"
    local uid="${USER_ID:-}"
    case "$skill" in
        safety-auth-app|safety-auth-app:*)
            # N1001063(정병오) 전용
            [ "$uid" != "N1001063" ]
            ;;
        *)
            return 1  # 제한 없음
            ;;
    esac
}

# ─── Skill 탐색 ─────────────────────────────────────────────
# 등록된 모든 skill 파일명(확장자 제거)을 반환. Claude Code는
# ~/.claude/commands/*.md 와 ~/.claude/plugins/cache/*/*/*/commands/*.md 를
# 슬래시 명령으로 사용 가능하게 등록함.
# 제한된 skill 은 현재 사용자가 허용 대상이 아니면 목록에서 제외.
list_skills() {
    local base
    {
        # 사용자 설치 skill (commands 디렉토리)
        if [ -d "$HOME/.claude/commands" ]; then
            find "$HOME/.claude/commands" -maxdepth 3 -type f \( -name '*.md' -o -name '*.sh' \) 2>/dev/null \
                | while read -r f; do
                    rel="${f#$HOME/.claude/commands/}"
                    rel="${rel%.md}"
                    rel="${rel%.sh}"
                    # 디렉토리 구분자는 콜론으로 (Claude Code 네임스페이스 규칙)
                    echo "${rel//\//:}"
                done
        fi
        # 플러그인 번들 skill
        if [ -d "$HOME/.claude/plugins/cache" ]; then
            find "$HOME/.claude/plugins/cache" -path '*/commands/*.md' -type f 2>/dev/null \
                | while read -r f; do
                    # .../plugin-name/version/commands/name.md → plugin:name
                    plugin=$(echo "$f" | sed -n 's|.*/\([^/]*\)/[^/]*/commands/.*|\1|p')
                    name=$(basename "$f" .md)
                    [ -n "$plugin" ] && echo "${plugin}:${name}" || echo "$name"
                done
        fi
    } | sort -u | while read -r skill; do
        # 제한된 skill 은 현재 USER_ID 에 허용되지 않으면 목록에서 숨김
        if skill_is_restricted "$skill"; then
            continue
        fi
        echo "$skill"
    done
}

show_banner() {
    clear || true
    printf "${CB}"
    cat << 'HEADER'
 ╭──────────────────────────────────────────────────────╮
 │  Claude Code — 답변만 받기 모드                     │
 │                                                      │
 │  질문을 입력하고 Enter — 최종 답변만 표시합니다.    │
 │  '/' 로 시작하는 슬래시 명령 사용 가능.             │
 ╰──────────────────────────────────────────────────────╯
HEADER
    printf "${CR}\n"
    printf "${CH} 내장 명령    : /help  /new  /switch  /db  /exit\n"
    printf " 등록된 스킬  : '/' 또는 '/help' 입력으로 전체 목록 보기\n"
    printf " 참고         : 각 질문은 독립 세션 (이전 대화 미연속)${CR}\n"
}

show_builtin() {
    printf "\n${CB}내장 명령${CR}\n"
    printf "  ${CP}/${CR} 또는 ${CP}/help${CR}   이 도움말 + 등록된 스킬 전체 목록\n"
    printf "  ${CP}/clear${CR}           화면 지우기\n"
    printf "  ${CP}/new${CR}  /reset     새 대화 (배너 재표시)\n"
    printf "  ${CP}/switch${CR}          '진행 과정 자세히 보기' 모드로 전환\n"
    printf "  ${CP}/db${CR}              DB 접속 명령 안내\n"
    printf "  ${CP}/files${CR}           파일 경로 안내\n"
    printf "  ${CP}/report${CR} ${CH}주제${CR}     간단 보고서 생성 요청\n"
    printf "  ${CP}/excel${CR} ${CH}주제${CR}      엑셀 추출 요청\n"
    printf "  ${CP}/exit${CR}            종료 (exit / quit / Ctrl+D)\n"
}

show_skills() {
    printf "\n${CB}등록된 스킬 (슬래시 명령)${CR}\n"
    local skills
    skills=$(list_skills)
    if [ -z "$skills" ]; then
        printf "${CH}  (등록된 스킬 없음)${CR}\n"
        return
    fi
    # 3열 형태로 정렬 출력
    echo "$skills" | awk -v CP="$CP" -v CR="$CR" '
        { cmds[NR] = $0 }
        END {
            n = NR
            cols = 3
            rows = int((n + cols - 1) / cols)
            for (r = 0; r < rows; r++) {
                line = ""
                for (c = 0; c < cols; c++) {
                    i = c * rows + r + 1
                    if (i <= n) {
                        line = line sprintf("  %s/%-28s%s", CP, cmds[i], CR)
                    }
                }
                print line
            }
        }'
    printf "\n${CH} 사용 예:  /gstack:browse   /sc:implement \"기능 설명\"${CR}\n"
    printf "${CH} (스킬은 claude 에 전달되어 실행됩니다)${CR}\n"
}

show_tui_only() {
    printf "\n${CB}TUI 전용 명령${CR} ${CH}(답변만 받기 모드 불가 → /switch 후 사용)${CR}\n"
    printf "  ${CH}/plugins   /mcp       /model     /compact   /config\n"
    printf "  /cost      /doctor    /status    /init      /ide\n"
    printf "  /logout    /login     /memory    /hooks     /permissions${CR}\n"
}

show_help() {
    show_builtin
    show_skills
    show_tui_only
}

show_db_help() {
    printf "\n${CB}사내 DB 접속 명령${CR}\n"
    printf "  ${CP}psql-tango${CR}     TANGO 알람 DB (고장/알람/업무일지)\n"
    printf "  ${CP}psql \$DATABASE_URL${CR}  안전관리 DB (TBM/작업/순찰)\n"
    printf "  ${CP}psql-doculog${CR}   Docu-Log 문서활동 분석 DB\n"
    printf "\n${CH} 자연어로 질문하면 Claude가 적절한 DB에 쿼리합니다.\n"
    printf "   예: '오늘 경남 지역 고장 건수 알려줘'${CR}\n"
}

show_files_help() {
    printf "\n${CB}파일 관리${CR}\n"
    printf "  업로드된 파일: ~/workspace/uploads/\n"
    printf "  보고서       : ~/workspace/reports/\n"
    printf "  엑셀 추출    : ~/workspace/exports/\n"
    printf "  파일 다운로드: 브라우저 /files/ 페이지\n"
}

# 입력된 슬래시 명령이 등록된 skill 중 하나인지 확인.
# true → 해당 이름 반환, false → 빈 문자열.
is_registered_skill() {
    local query="$1"
    list_skills | grep -Fx -- "$query" >/dev/null 2>&1 && echo "$query"
}

show_banner

trap 'echo ""; echo "  세션을 종료합니다..."; exit 0' INT

while true; do
    printf "\n${CP}질문>${CR} "
    if ! IFS= read -r QUESTION; then
        echo ""
        break
    fi

    # 공백 정리
    QUESTION="${QUESTION#"${QUESTION%%[![:space:]]*}"}"
    QUESTION="${QUESTION%"${QUESTION##*[![:space:]]}"}"
    [ -z "$QUESTION" ] && continue

    # ─── 내장 슬래시 명령 ─────────────────────────────────
    case "$QUESTION" in
        /|/help|/\?|help|도움말)
            show_help
            continue
            ;;
        /clear|clear)
            show_banner
            continue
            ;;
        /new|/reset|새대화)
            show_banner
            printf "${CH}  새 대화로 시작합니다.${CR}\n"
            continue
            ;;
        /switch|/mode|/진행|switch)
            /usr/local/bin/switch-mode 2
            printf "${CW}  브라우저를 새로고침하면 '진행 과정 자세히 보기' 모드로 시작됩니다.${CR}\n"
            printf "${CH}  (상단 바의 '진행과정 자세히 보기' 버튼을 눌러도 됩니다)${CR}\n"
            exit 0
            ;;
        /db|/database|db)
            show_db_help
            continue
            ;;
        /files|/file)
            show_files_help
            continue
            ;;
        /exit|/quit|exit|quit|:q|종료)
            echo "  종료합니다."
            break
            ;;
        /report*|/excel*)
            CMD="${QUESTION%% *}"
            SUBJ="${QUESTION#"$CMD"}"
            SUBJ="${SUBJ#"${SUBJ%%[![:space:]]*}"}"
            if [ -z "$SUBJ" ]; then
                printf "${CW}  사용법: ${CMD} <주제>\n  예: ${CMD} 3월 TBM 실적${CR}\n"
                continue
            fi
            case "$CMD" in
                /report) QUESTION="\"${SUBJ}\" 주제로 간단한 보고서를 작성해줘." ;;
                /excel)  QUESTION="\"${SUBJ}\" 데이터를 엑셀 파일로 추출해서 ~/workspace/exports/ 에 저장해줘." ;;
            esac
            ;;
        /*)
            FIRST="${QUESTION%% *}"         # /gstack:browse
            SKILL_NAME="${FIRST#/}"         # gstack:browse
            # Claude Code TUI 전용 명령 — -p 모드에선 동작 안 함
            case "$FIRST" in
                /plugins|/mcp|/model|/compact|/config|/cost|/doctor|/status|/init|/ide|/logout|/login|/memory|/bug|/release-notes|/hooks|/permissions|/terminal-setup|/vim)
                    printf "${CW}  '${FIRST}' 는 'claude' TUI 전용 명령입니다 (답변만 받기 모드에서는 사용 불가).${CR}\n"
                    printf "${CH}  사용하려면 ${CP}/switch${CH} 로 '진행 과정 자세히 보기' 모드로 전환 후 claude TUI에서 실행하세요.${CR}\n"
                    continue
                    ;;
            esac
            # 제한된 skill 을 명시적으로 입력한 경우 — 접근 불가 안내
            if skill_is_restricted "$SKILL_NAME"; then
                printf "${CW}  '${FIRST}' 스킬은 특별 허용된 사용자만 사용할 수 있습니다.${CR}\n"
                printf "${CH}  권한 요청은 플랫폼 관리자(N1102359)에게 문의해 주세요.${CR}\n"
                continue
            fi
            # 등록된 skill 이면 그대로 claude 에 전달 (claude -p "/skill args")
            if [ -n "$(is_registered_skill "$SKILL_NAME")" ]; then
                : # QUESTION 그대로 claude 에 전달
            else
                printf "${CW}  알 수 없는 명령: ${QUESTION}${CR}\n"
                printf "${CH}  '/' 또는 '/help' 로 사용 가능한 명령·스킬을 확인하세요.${CR}\n"
                continue
            fi
            ;;
    esac

    # ─── 실제 Claude 호출 ─────────────────────────────────
    echo ""
    if ! claude -p "$QUESTION" --output-format text 2>&1; then
        echo ""
        printf "${CE}  (오류가 발생했습니다. 다시 시도해주세요.)${CR}\n"
    fi
    echo ""
done
