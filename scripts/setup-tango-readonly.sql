-- =============================================================================
-- TANGO DB: Read-Only User for Claude Code Pods
--
-- 실행 대상: aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com (postgres DB)
-- 실행 방법:
--   psql -h aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com \
--        -U postgres -d postgres -f scripts/setup-tango-readonly.sql
--
-- 이 스크립트는 Claude Code Pod에서 TANGO 알람 데이터를 조회할 수 있도록
-- claude_readonly 사용자를 생성합니다.
-- =============================================================================

-- 1. Read-Only 사용자 생성
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'claude_readonly') THEN
        CREATE ROLE claude_readonly WITH LOGIN PASSWORD 'TangoReadOnly2026!';
    END IF;
END
$$;

-- 2. 기본 연결 권한
GRANT CONNECT ON DATABASE postgres TO claude_readonly;
GRANT USAGE ON SCHEMA public TO claude_readonly;

-- 3. 기존 테이블 읽기 권한 (알람 관련 테이블)
GRANT SELECT ON alarm_data TO claude_readonly;
GRANT SELECT ON alarm_events TO claude_readonly;
GRANT SELECT ON alarm_history TO claude_readonly;
GRANT SELECT ON alarm_raw_logs TO claude_readonly;
GRANT SELECT ON alarm_hourly_summary TO claude_readonly;
GRANT SELECT ON facility_info TO claude_readonly;

-- 4. 뷰 읽기 권한
GRANT SELECT ON alarm_statistics TO claude_readonly;

-- 5. 향후 생성되는 테이블에도 자동으로 읽기 권한 부여
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO claude_readonly;

-- 6. 확인
\echo '=== claude_readonly user setup complete ==='
\echo 'Connection test:'
\echo '  psql -h aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com -U claude_readonly -d postgres'
