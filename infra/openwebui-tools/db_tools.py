"""
title: DB Query
description: 사내 DB 조회 (TANGO 알람, Safety 관리, DocuLog). 모델이 "데이터베이스", "알람", "통계" 등 DB 조회 의도를 파악하면 자동 호출.
author: SK ONS Bedrock AI Platform
author_url: https://github.com/cation98/bedrock-ai-agent
version: 0.1.0
requirements: psycopg2-binary

Phase 1 Option A 구현 — 쿼리 로직을 모듈 단위로 격리하여 Phase 2 MCP 서버로 이식 용이하게 설계.
참조: mindbase/strategy-db-access-openwebui-mcp-migration

Claude Code의 `/db` 스킬(container-image/config/skills/db.md)과 동일 DB·같은 스키마.

환경변수 (open-webui Pod에 주입 — Secret `openwebui-db-readonly` 참조):
  - SAFETY_DATABASE_URL: postgresql://claude_readonly:.../safety (safety-prod-db-readonly)
  - TANGO_DATABASE_URL : postgresql://claude_readonly:...@aiagentdb/postgres (TANGO 알람)
  - DOCULOG_DATABASE_URL: postgresql://doculog_reader:...@aiagentdb/postgres (문서 로그)
"""

import os
from typing import Optional

import psycopg2
import psycopg2.extras


_MAX_ROWS = 50          # 결과 행 수 제한 (모델 컨텍스트 과적재 방지)
_MAX_RESULT_CHARS = 8000  # 결과 문자열 크기 제한


def _require_select_only(sql: str) -> Optional[str]:
    """readonly 정책 — SELECT/WITH 외 문장 차단.
    :return: 위반 사유 문자열(차단 시) 또는 None(허용).
    """
    stripped = sql.strip().lower()
    # 세미콜론 이후 추가 statement 차단
    if ";" in stripped.rstrip(";"):
        return "다중 statement 금지 — 단일 SELECT/WITH 만 허용"
    if not (stripped.startswith("select") or stripped.startswith("with")):
        return "SELECT 또는 WITH 로 시작하는 readonly 쿼리만 허용"
    forbidden = (
        "insert", "update", "delete", "drop", "truncate", "alter",
        "create", "grant", "revoke", "copy", "call",
    )
    for kw in forbidden:
        if f" {kw} " in f" {stripped} ":
            return f"쓰기/DDL 키워드 차단: {kw}"
    return None


def _format_result(cols, rows) -> str:
    """마크다운 테이블로 포맷. 행수·문자수 제한."""
    truncated = False
    if len(rows) > _MAX_ROWS:
        rows = rows[:_MAX_ROWS]
        truncated = True
    if not rows:
        return "(결과 없음)"
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = [
        "| " + " | ".join(
            str(v)[:200] if v is not None else "NULL" for v in row
        ) + " |"
        for row in rows
    ]
    text = "\n".join([header, sep] + body)
    if len(text) > _MAX_RESULT_CHARS:
        text = text[:_MAX_RESULT_CHARS] + "\n... (결과 길이 제한 초과)"
    if truncated:
        text += f"\n(상위 {_MAX_ROWS}행만 표시)"
    return text


def _run_select(env_key: str, sql: str, db_name: str) -> str:
    """쿼리 실행 공통 로직. env 자격증명으로 접속 → 결과 포맷.
    Phase 2 MCP 이식 시 이 함수 시그니처가 MCP tool entry 로 매핑됨.
    """
    url = os.environ.get(env_key)
    if not url:
        return f"Error: {env_key} 환경변수 미설정 — 관리자에게 문의"
    gate = _require_select_only(sql)
    if gate:
        return f"Error: {gate}"
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description is None:
                conn.close()
                return "(SELECT가 아님)"
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        header = f"### {db_name} 조회 결과 ({len(rows)}행)"
        return header + "\n\n" + _format_result(cols, rows)
    except psycopg2.Error as e:
        return f"Error ({db_name}): {e.pgerror or str(e)}"
    except Exception as e:
        return f"Error ({db_name}): {e}"


class Tools:
    def __init__(self):
        # Open WebUI Tools 프레임워크 규격
        pass

    def query_tango(self, sql: str) -> str:
        """
        TANGO 네트워크 알람 DB 조회. 네트워크 장비 고장·복구·이벤트 로그.

        주요 테이블:
          alarm_data (현재 활성 고장, 7일 보존)
            - OP_TEAM_ORG_NM: 운영팀
            - OP_HDOFC_ORG_NM: 본부
          alarm_statistics (뷰 — 팀별 요약 — 빠른 조회)
          alarm_events (전체 이벤트, 30일)
            - received_at: 발생시각
          alarm_history (복구 이력)

        예시:
          query_tango("SELECT * FROM alarm_statistics ORDER BY alarm_count DESC LIMIT 10")
          query_tango("SELECT * FROM alarm_data WHERE OP_TEAM_ORG_NM LIKE '%김해%'")

        제약: SELECT/WITH만 허용, 단일 statement, 최대 50행 반환.

        :param sql: PostgreSQL SELECT 쿼리
        :return: 마크다운 테이블 결과
        """
        return _run_select("TANGO_DATABASE_URL", sql, "TANGO")

    def query_safety(self, sql: str) -> str:
        """
        Safety(안전관리) DB 조회 — 산업안전 데이터.

        환경변수: SAFETY_DATABASE_URL

        예시:
          query_safety("SELECT COUNT(*) FROM incidents WHERE occurred_at > NOW() - INTERVAL '30 days'")

        제약: SELECT/WITH만 허용, 단일 statement, 최대 50행 반환.

        :param sql: PostgreSQL SELECT 쿼리
        :return: 마크다운 테이블 결과
        """
        return _run_select("SAFETY_DATABASE_URL", sql, "Safety")

    def query_doculog(self, sql: str) -> str:
        """
        DocuLog(문서 활동 로그) DB 조회.

        환경변수: DOCULOG_DATABASE_URL

        제약: SELECT/WITH만 허용, 단일 statement, 최대 50행 반환.

        :param sql: PostgreSQL SELECT 쿼리
        :return: 마크다운 테이블 결과
        """
        return _run_select("DOCULOG_DATABASE_URL", sql, "DocuLog")
