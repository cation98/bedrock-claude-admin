"""
title: DB Query
description: 사내 DB(TANGO 알람·Safety 안전관리·DocuLog 문서활동) 조회. 사용자가 데이터·통계·알람·안전·문서 등을 물으면 반드시 해당 tool을 호출해 실제 데이터를 조회한 후 답변. 추측·기억에 의존하지 말고 반드시 query_* tool을 사용.
author: SK ONS Bedrock AI Platform
author_url: https://github.com/cation98/bedrock-ai-agent
version: 0.2.0
requirements: psycopg2-binary

Phase 1 Option A — Open WebUI 에서 Claude Code `/db` 스킬과 동일 기능 제공.
쿼리 로직은 _run_select 공통 진입점으로 격리하여 Phase 2 MCP 서버 이식 용이.
"""

import os
from typing import Optional

import psycopg2
import psycopg2.extras  # noqa: F401  # ensure extras loadable (Open WebUI runtime check)


_MAX_ROWS = 50
_MAX_RESULT_CHARS = 8000


def _require_select_only(sql: str) -> Optional[str]:
    stripped = sql.strip().lower()
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


_DB_ENV_MAP = {
    "tango": "TANGO_DATABASE_URL",
    "safety": "SAFETY_DATABASE_URL",
    "doculog": "DOCULOG_DATABASE_URL",
}


class Tools:
    def __init__(self):
        pass

    def describe_table(self, db: str, table_name: str) -> str:
        """지정 DB의 특정 테이블 컬럼 목록을 반환. 스키마가 불확실할 때 먼저 호출하여
        컬럼 이름·타입을 확인한 뒤 query_* 로 정식 쿼리.

        :param db: "tango" | "safety" | "doculog"
        :param table_name: 테이블명 (예: "safety_activity_tbmactivity")
        :return: column_name:data_type 목록
        """
        env_key = _DB_ENV_MAP.get(db.lower())
        if not env_key:
            return f"Error: db='{db}' 지원 안함. 'tango'|'safety'|'doculog' 중 선택."
        # readonly introspection 쿼리
        sql = (
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_name = '{table_name}' ORDER BY ordinal_position"
        )
        # information_schema는 시스템 테이블이므로 _require_select_only 는 통과함
        return _run_select(env_key, sql, f"describe {db}.{table_name}")

    def query_tango(self, sql: str) -> str:
        """TANGO 네트워크 알람 DB 조회 (PostgreSQL). 네트워크 장비 고장·복구·이벤트.

        주요 테이블 (정확한 이름 사용 필수):
          - alarm_data      : 현재 활성 고장 (실시간, 7일 보존)
          - alarm_events    : 전체 이벤트 로그 (30일)
          - alarm_history   : 복구된 고장 이력
          - alarm_statistics: 팀별 요약 뷰 (team_name, alarm_count, new_alarms, unacked_alarms, locked_alarms, latest_alarm)
          - facility_info   : 장비 마스터 (JSONB)
          - alarm_hourly_summary : 시간대별 집계

          Opark 업무일지 (같은 DB에 병존):
          - opark_daily_report  : 실시간 업무일지 (~183K, 1분 upsert)
          - opark_daily_archive : 과거 아카이브 (~1.8M)
          - report_embeddings   : pgvector 768dim (ko-sroberta)
          - report_ontology     : 5단계 업무 분류 (level/code/parent_code)
          - report_alarm_matches: 알람-업무 유사도 매칭

        주요 컬럼 (alarm_data):
          OP_TEAM_ORG_NM(운용팀명), OP_HDOFC_ORG_NM(본부명), EQP_NM(장비명),
          FALT_OCCR_LOC_CTT(고장위치), EVT_TIME(발생시각), ALM_STAT_VAL(상태),
          ALM_DESC(알람설명), MCP_NM(시/도), SGG_NM(시/군/구), LDONG_NM(동), EQP_ID

        알람 상태값:
          활성: O(발생), U(미확인), L(잠금)  /  해제: C,F,A,D

        Opark 기간 선택 원칙:
          - 최근 데이터 → opark_daily_report (created_at 기준)
          - 과거 아카이브 → opark_daily_archive (archived_at 기준)
          - 사용자가 기간 불명확하면 되물어 확인

        예시:
          query_tango("SELECT * FROM alarm_statistics ORDER BY alarm_count DESC LIMIT 10")
          query_tango("SELECT OP_TEAM_ORG_NM, COUNT(*) FROM alarm_data WHERE OP_HDOFC_ORG_NM LIKE '%경남%' GROUP BY 1 ORDER BY 2 DESC")
          query_tango("SELECT date_trunc('hour', received_at) AS hour, COUNT(*) FROM alarm_events WHERE received_at > NOW() - INTERVAL '24 hours' GROUP BY 1 ORDER BY 1")

        제약: SELECT/WITH만 허용, 단일 statement, 최대 50행, 8KB.
        :param sql: PostgreSQL SELECT 쿼리
        :return: 마크다운 테이블 결과
        """
        return _run_select("TANGO_DATABASE_URL", sql, "TANGO")

    def query_safety(self, sql: str) -> str:
        """Safety(안전관리) DB 조회 (PostgreSQL, DB명: safety). 산업안전 전반.

        주요 테이블 (카테고리별, **실제 테이블명** - 축약명 추측 금지):

          TBM(작업전 안전미팅):
            safety_activity_tbmactivity            ← "tbm" 아님! 이 이름 사용 필수
            safety_activity_tbmactivity_companion  (동행자)
            safety_activity_tbmactivityimages      (사진)

          작업정보:
            safety_activity_workinfo       (region_sko, team 등)
            safety_activity_workstatus
            safety_activity_workstatushistory
            safety_activity_worktype

          작업중지:
            safety_activity_workstophistory
            safety_activity_workstophistoryimages

          순찰점검:
            safety_activity_patrolsafetyinspection
            safety_activity_patrolsafetyinspectchecklist
            safety_activity_patrolsafetyinspectiongoodandbad
            safety_activity_patrolsafetyjointinspection

          주간계획:
            safety_activity_weeklyworkplanfrombp
            safety_activity_weeklyworkplanperskoregion
            safety_activity_weeklyworkplanperskoteam

          SHE 측정:
            she_measurement_sherecord
            she_measurement_shecategory
            she_measurement_sheitemscore

          컴플라이언스:
            compliance_check_checklistrecord
            compliance_check_checklistitem

          위험성 평가:
            committee_workriskassessment

          게시판:
            board_post / board_comment / board_file

          사용자·조직:
            auth_user (username=사번)
            accounts_userprofile (region_name, team_name, job_name)
            sysmanage_region / sysmanage_teamregion / sysmanage_companymaster

        존재하지 않는 테이블명 추측 금지 — 반드시 위 목록의 정확한 이름 사용.

        예시:
          query_safety("SELECT w.region_sko, COUNT(*) FROM safety_activity_tbmactivity t JOIN safety_activity_workinfo w ON t.work_id_id = w.id WHERE DATE(t.created_at) = CURRENT_DATE GROUP BY 1 ORDER BY 2 DESC")
          query_safety("SELECT status, COUNT(*) FROM safety_activity_workstatus GROUP BY 1")
          query_safety("SELECT * FROM safety_activity_tbmactivity ORDER BY created_at DESC LIMIT 4")

        제약: SELECT/WITH만 허용, 단일 statement, 최대 50행.
        :param sql: PostgreSQL SELECT 쿼리
        :return: 마크다운 테이블 결과
        """
        return _run_select("SAFETY_DATABASE_URL", sql, "Safety")

    def query_doculog(self, sql: str) -> str:
        """DocuLog 문서활동 분석 DB 조회. 267일간 4.6M+건 문서 활동 로그.

        주요 테이블:
          document_logs     : 문서활동 로그 원본 + 분석 컬럼 (4,616,363건)
          task_embeddings   : 업무명 임베딩 768dim (359,968건)
          mv_pre_reorg      : 2025년 개편 전 데이터 뷰 (4,037,324건)

        핵심 컬럼(document_logs):
          fn_task_normalized : 날짜/버전 제거된 업무명 (핵심 분석 단위)
          fn_doc_type        : 문서 유형 (현황/보고서/점검/계획 등 13종)
          department         : 소속 부서 (192개)
          dept_function      : 부서 기능 (품질혁신, Access관제 등)
          dept_region        : 부서 지역 (서울, 경남 등 18개)
          log_type           : 활동 유형 (편집, 생성, 읽기 등)

        예시:
          query_doculog("SELECT fn_task_normalized, COUNT(*) FROM document_logs WHERE dept_function = '품질혁신' GROUP BY 1 ORDER BY 2 DESC LIMIT 10")
          query_doculog("SELECT dept_function, COUNT(DISTINCT fn_task_normalized) FROM document_logs GROUP BY 1 ORDER BY 2 DESC")

        제약: SELECT/WITH만 허용, 단일 statement, 최대 50행.
        :param sql: PostgreSQL SELECT 쿼리
        :return: 마크다운 테이블 결과
        """
        return _run_select("DOCULOG_DATABASE_URL", sql, "DocuLog")
