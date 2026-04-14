#!/bin/bash
# ask — 최종 답변만 출력하는 Claude Code one-shot 래퍼
# 사용법: ask "질문 내용"
# 진행 로그/도구 호출/사고 과정 없이 결과 텍스트만 표시.

if [ $# -eq 0 ]; then
    cat << USAGE
사용법: ask "질문 내용"

예시:
  ask "오늘 TBM 건수 알려줘"
  ask "어제 고장 많이 난 팀 top 5"

각 질문은 독립 세션입니다 — 이전 대화는 이어지지 않습니다.
진행 과정까지 보려면 'claude' 를 실행하거나 'switch-mode 2' 로 전환.
USAGE
    exit 1
fi

exec claude -p "$@" --output-format text
