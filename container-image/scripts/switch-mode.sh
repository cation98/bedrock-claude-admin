#!/bin/bash
# switch-mode [1|2] — Claude 터미널 시작 모드 전환
#   1 = 답변만 받기 (기본)
#   2 = 진행 과정 자세히 보기
# 파일 ~/.claude-mode 에 저장. 다음 Claude 세션 시작 시 반영.

MODE_FILE="$HOME/.claude-mode"
NEW_MODE="${1:-}"

if [ -z "$NEW_MODE" ]; then
    CURRENT=$(cat "$MODE_FILE" 2>/dev/null || echo "1")
    case "$CURRENT" in
        1) CURRENT_LABEL="답변만 받기" ;;
        2) CURRENT_LABEL="진행 과정 자세히 보기" ;;
        *) CURRENT_LABEL="미설정" ;;
    esac
    cat << USAGE
현재 모드: ${CURRENT_LABEL}

사용법:
  switch-mode 1     답변만 받기 모드로 전환
  switch-mode 2     진행 과정 자세히 보기 모드로 전환

변경 후 브라우저를 새로고침해야 적용됩니다.
USAGE
    exit 0
fi

if [ "$NEW_MODE" != "1" ] && [ "$NEW_MODE" != "2" ]; then
    echo "오류: 모드는 1 또는 2 여야 합니다." >&2
    exit 1
fi

echo "$NEW_MODE" > "$MODE_FILE"
case "$NEW_MODE" in
    1) echo "✓ 답변만 받기 모드로 설정했습니다." ;;
    2) echo "✓ 진행 과정 자세히 보기 모드로 설정했습니다." ;;
esac
echo "브라우저를 새로고침하면 적용됩니다."
