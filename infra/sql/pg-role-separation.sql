-- ============================================================
-- PostgreSQL Role Separation for Bedrock Claude Code Platform
-- ============================================================
-- Created: 2026-03-27
-- Purpose: Per-security-level DB roles restricting table access
--
-- OVERVIEW
-- --------
-- Three RDS instances serve the platform:
--
--   1. TANGO DB  (aiagentdb / postgres)
--      - Alarm management, facility info, O-Park reports
--
--   2. Docu-Log DB  (aiagentdb / doculog)
--      - Document activity logs, task embeddings
--
--   3. Safety DB  (safety-prod-db-readonly / safety)
--      - Safety management system (READ REPLICA -- cannot create roles)
--
-- ROLE HIERARCHY
-- --------------
--   "full" level   -> claude_readonly (TANGO), doculog_reader (Docu-Log)
--                     Full SELECT on all tables in respective databases.
--
--   "standard" level -> claude_tango_std (TANGO), claude_doculog_std (Docu-Log)
--                       SELECT on a curated subset; raw logs excluded.
--
--   Safety DB       -> claude_readonly for ALL levels (read-replica limitation).
--                      CLAUDE.md section filtering is the primary access control.
--
-- BLACKLISTED TABLES (never accessible to standard roles)
-- -------------------------------------------------------
--   TANGO:   auth_user, accounts_userprofile, django_session,
--            django_admin_log, alarm_raw_logs, authtoken_token,
--            rest_authtoken_authtoken, rest_authtoken_emailconfirmationtoken,
--            history_userfilterhistory, history_userloghistory,
--            auth_group, auth_group_permissions, auth_permission,
--            auth_user_groups, auth_user_user_permissions,
--            django_content_type, django_migrations
--
--   Platform DB (bedrock_platform): No CONNECT granted to any role.
--


-- ============================================================
-- 1. TANGO DB (aiagentdb / postgres)
-- ============================================================

-- 1a. Standard-level role: alarm + opark + reports (curated subset)
CREATE USER claude_tango_std WITH PASSWORD 'TangoStd2026!' CONNECTION LIMIT 20;
GRANT CONNECT ON DATABASE postgres TO claude_tango_std;
GRANT USAGE ON SCHEMA public TO claude_tango_std;

-- Alarm tables (excluding alarm_raw_logs)
GRANT SELECT ON alarm_data, alarm_events, alarm_history, alarm_hourly_summary
    TO claude_tango_std;

-- Facility + O-Park tables
GRANT SELECT ON facility_info, opark_daily_report, opark_daily_archive
    TO claude_tango_std;

-- Report tables
GRANT SELECT ON report_ontology, report_alarm_matches, report_embeddings, reports
    TO claude_tango_std;

-- Cross-database isolation: prevent connecting to doculog
REVOKE CONNECT ON DATABASE doculog FROM claude_tango_std;


-- ============================================================
-- 2. Docu-Log DB (aiagentdb / doculog)
-- ============================================================

-- 2a. Standard-level role: document_logs + task_embeddings only
CREATE USER claude_doculog_std WITH PASSWORD 'DocuLogStd2026!' CONNECTION LIMIT 20;
GRANT CONNECT ON DATABASE doculog TO claude_doculog_std;
GRANT USAGE ON SCHEMA public TO claude_doculog_std;
GRANT SELECT ON document_logs, task_embeddings TO claude_doculog_std;

-- Cross-database isolation: prevent connecting to tango (postgres)
REVOKE CONNECT ON DATABASE postgres FROM claude_doculog_std;


-- ============================================================
-- 3. Safety DB (safety-prod-db-readonly / safety)
-- ============================================================
-- NOTE: This is a READ REPLICA. Cannot CREATE USER on it.
--
-- Current state:
--   claude_readonly  -- Full SELECT on all tables (created on primary)
--
-- To add standard-level roles, a DBA must run the following on the
-- PRIMARY instance (safety-prod-db). The role will then replicate
-- to the read replica automatically.
--
-- Example (run on PRIMARY only):
--
--   CREATE USER claude_safety_std WITH PASSWORD '...' CONNECTION LIMIT 20;
--   GRANT CONNECT ON DATABASE safety TO claude_safety_std;
--   GRANT USAGE ON SCHEMA public TO claude_safety_std;
--   GRANT SELECT ON <curated_table_list> TO claude_safety_std;
--
-- Until then, all security levels use claude_readonly for Safety DB,
-- and CLAUDE.md section filtering serves as the primary access control.


-- ============================================================
-- VERIFICATION QUERIES
-- ============================================================

-- Test claude_tango_std (should succeed):
--   SELECT COUNT(*) FROM alarm_data;
--   SELECT COUNT(*) FROM opark_daily_report;
--   SELECT COUNT(*) FROM reports;

-- Test claude_tango_std (should FAIL with "permission denied"):
--   SELECT COUNT(*) FROM alarm_raw_logs;
--   SELECT COUNT(*) FROM auth_user;
--   SELECT COUNT(*) FROM django_session;

-- Test claude_doculog_std (should succeed):
--   SELECT COUNT(*) FROM document_logs;
--   SELECT COUNT(*) FROM task_embeddings;

-- Test cross-database isolation:
--   claude_doculog_std connecting to postgres DB -> should fail
--   claude_tango_std connecting to doculog DB   -> should fail


-- ============================================================
-- K8S SECRET MAPPING
-- ============================================================
-- auth-gateway-secrets (namespaces: platform, claude-sessions):
--   TANGO_DB_PASSWORD_STD   = TangoStd2026!
--   DOCULOG_DB_PASSWORD_STD = DocuLogStd2026!
--
-- Existing secrets (unchanged):
--   TANGO_DB_PASSWORD       = (claude_readonly password, "full" level)
--   DOCULOG_DB_PASSWORD     = (doculog_reader password, "full" level)
