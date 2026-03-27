-- =============================================================================
-- PostgreSQL Access Control Design
-- 3-Tier Security Model for Bedrock AI Agent Platform
--
-- Author: Backend Architect
-- Date: 2026-03-27
-- Status: DESIGN ONLY — DO NOT EXECUTE without review
--
-- Overview:
--   Two RDS instances, three security levels (basic, standard, full).
--   Each level gets progressively more table access.
--   The bedrock_platform database is NEVER accessible to any Pod user.
--
-- RDS Instances:
--   1. safety-prod-db-readonly  (Safety Management DB, Django)
--   2. aiagentdb                (TANGO + Opark, database "postgres")
--      Also hosts: database "bedrock_platform" (platform-internal, off-limits)
--
-- =============================================================================


-- #############################################################################
-- SECTION 0: BLACKLIST — Tables that must NEVER be accessible from Pods
-- #############################################################################
--
-- These tables contain PII, authentication tokens, or platform internals.
-- Even "full" level users must not reach them.
--
-- On safety-prod-db-readonly:
--   auth_user                         — usernames, emails, hashed passwords
--   accounts_userprofile              — phone numbers, personal info
--   accounts_passwordhistory          — password change history
--   django_session                    — active session tokens
--   django_admin_log                  — admin audit trail (contains user refs)
--   authtoken_token                   — DRF API tokens (if exists)
--
-- On aiagentdb (database "bedrock_platform"):
--   ALL tables                        — users, terminal_sessions, sms_logs, etc.
--   This entire database must have no CONNECT grant for any Pod user.
--
-- On aiagentdb (database "postgres"):
--   No sensitive tables identified currently. All tables are operational data.
--   If user-facing tables are added later, re-evaluate.
--


-- #############################################################################
-- SECTION 1: ROLE HIERARCHY
-- #############################################################################
--
-- Role inheritance diagram:
--
--   claude_base_ro          (abstract, NOLOGIN — shared read-only settings)
--     |
--     +-- claude_safety_ro   (LOGIN — standard level, safety DB)
--     +-- claude_tango_ro    (LOGIN — standard level, tango DB)
--     +-- claude_readonly    (LOGIN — full level, both DBs; already exists)
--


-- =============================================================================
-- 1A. SAFETY-PROD-DB-READONLY instance
--     Execute connected to: safety-prod-db-readonly, database "safety"
-- =============================================================================

-- Base role: shared read-only properties, no login
-- All Pod PG users inherit from this.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'claude_base_ro') THEN
        CREATE ROLE claude_base_ro NOLOGIN;
        COMMENT ON ROLE claude_base_ro IS 'Abstract base role for Pod read-only access. No login.';
    END IF;
END $$;

-- "standard" level: limited table access on safety DB
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'claude_safety_ro') THEN
        CREATE ROLE claude_safety_ro WITH
            LOGIN
            PASSWORD 'CHANGE_ME_SafetyStd2026'  -- Generate a strong password before deploy
            CONNECTION LIMIT 10                   -- Max 10 concurrent connections
            IN ROLE claude_base_ro;
        COMMENT ON ROLE claude_safety_ro IS 'Standard-level: SELECT on approved safety tables only. No PII tables.';
    END IF;
END $$;

-- "full" level: wide table access on safety DB (but still no PII)
-- This role may already exist. If upgrading, just adjust grants.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'claude_safety_full_ro') THEN
        CREATE ROLE claude_safety_full_ro WITH
            LOGIN
            PASSWORD 'CHANGE_ME_SafetyFull2026'  -- Generate a strong password before deploy
            CONNECTION LIMIT 10
            IN ROLE claude_base_ro;
        COMMENT ON ROLE claude_safety_full_ro IS 'Full-level: SELECT on all safety tables EXCEPT PII/auth tables.';
    END IF;
END $$;


-- =============================================================================
-- 1B. AIAGENTDB instance
--     Execute connected to: aiagentdb, database "postgres"
-- =============================================================================

-- Base role (same pattern, but on a different RDS instance)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'claude_base_ro') THEN
        CREATE ROLE claude_base_ro NOLOGIN;
        COMMENT ON ROLE claude_base_ro IS 'Abstract base role for Pod read-only access. No login.';
    END IF;
END $$;

-- "standard" level: limited TANGO/Opark tables
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'claude_tango_ro') THEN
        CREATE ROLE claude_tango_ro WITH
            LOGIN
            PASSWORD 'CHANGE_ME_TangoStd2026'    -- Generate a strong password before deploy
            CONNECTION LIMIT 15
            IN ROLE claude_base_ro;
        COMMENT ON ROLE claude_tango_ro IS 'Standard-level: SELECT on approved TANGO alarm + Opark tables only.';
    END IF;
END $$;

-- "full" level: the existing claude_readonly user.
-- We tighten it by revoking ALTER DEFAULT PRIVILEGES (overly broad).
-- If claude_readonly already exists, we ALTER rather than CREATE.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'claude_readonly') THEN
        CREATE ROLE claude_readonly WITH
            LOGIN
            PASSWORD 'CHANGE_ME_TangoFull2026'
            CONNECTION LIMIT 15
            IN ROLE claude_base_ro;
    ELSE
        -- Ensure it inherits the base role
        GRANT claude_base_ro TO claude_readonly;
    END IF;
    COMMENT ON ROLE claude_readonly IS 'Full-level: SELECT on all TANGO/Opark tables in postgres DB.';
END $$;


-- #############################################################################
-- SECTION 2: REVOKE — Start from zero (principle of least privilege)
-- #############################################################################

-- =============================================================================
-- 2A. SAFETY-PROD-DB-READONLY — database "safety"
-- =============================================================================

-- Revoke everything first, then grant explicitly.
-- This ensures no leftover permissions from previous setups.

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM claude_safety_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM claude_safety_ro;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM claude_safety_ro;
REVOKE ALL ON SCHEMA public FROM claude_safety_ro;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM claude_safety_full_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM claude_safety_full_ro;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM claude_safety_full_ro;
REVOKE ALL ON SCHEMA public FROM claude_safety_full_ro;

-- Remove any dangerous default privileges that auto-grant on new tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM claude_safety_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM claude_safety_full_ro;


-- =============================================================================
-- 2B. AIAGENTDB — database "postgres"
-- =============================================================================

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM claude_tango_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM claude_tango_ro;
REVOKE ALL ON SCHEMA public FROM claude_tango_ro;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM claude_readonly;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM claude_readonly;
REVOKE ALL ON SCHEMA public FROM claude_readonly;

-- CRITICAL: Remove the existing dangerous default privilege from the old setup
-- (Line 38 of setup-tango-readonly.sql granted auto-SELECT on all future tables)
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM claude_readonly;


-- =============================================================================
-- 2C. AIAGENTDB — database "bedrock_platform"
--     Ensure NO Pod user can even connect.
-- =============================================================================

-- Execute this connected to database "bedrock_platform":
REVOKE CONNECT ON DATABASE bedrock_platform FROM claude_readonly;
REVOKE CONNECT ON DATABASE bedrock_platform FROM claude_tango_ro;
REVOKE CONNECT ON DATABASE bedrock_platform FROM claude_base_ro;
REVOKE CONNECT ON DATABASE bedrock_platform FROM PUBLIC;
-- Only the admin (postgres) user should connect to bedrock_platform.


-- #############################################################################
-- SECTION 3: GRANT — "standard" level
-- #############################################################################
--
-- "standard" users get SELECT on a curated whitelist of tables.
-- Each table is listed explicitly. No wildcards. No default privileges.
-- Adding a new table requires a conscious SQL change.
--

-- =============================================================================
-- 3A. SAFETY DB — claude_safety_ro (standard level)
--     Approved tables: operational safety data only. No PII.
-- =============================================================================

-- Schema access (required before any table access)
GRANT CONNECT ON DATABASE safety TO claude_safety_ro;
GRANT USAGE ON SCHEMA public TO claude_safety_ro;

-- TBM (Toolbox Meeting) tables
GRANT SELECT ON safety_activity_tbmactivity            TO claude_safety_ro;
GRANT SELECT ON safety_activity_tbmactivity_companion   TO claude_safety_ro;
GRANT SELECT ON safety_activity_tbmactivityimages       TO claude_safety_ro;

-- Work information
GRANT SELECT ON safety_activity_workinfo                TO claude_safety_ro;
GRANT SELECT ON safety_activity_workstatus              TO claude_safety_ro;
GRANT SELECT ON safety_activity_workstatushistory       TO claude_safety_ro;
GRANT SELECT ON safety_activity_worktype                TO claude_safety_ro;

-- Work stop (safety incidents)
GRANT SELECT ON safety_activity_workstophistory         TO claude_safety_ro;
GRANT SELECT ON safety_activity_workstophistoryimages   TO claude_safety_ro;

-- Patrol / inspection
GRANT SELECT ON safety_activity_patrolsafetyinspection             TO claude_safety_ro;
GRANT SELECT ON safety_activity_patrolsafetyinspectchecklist       TO claude_safety_ro;
GRANT SELECT ON safety_activity_patrolsafetyinspectiongoodandbad   TO claude_safety_ro;
GRANT SELECT ON safety_activity_patrolsafetyjointinspection        TO claude_safety_ro;

-- Weekly work plans
GRANT SELECT ON safety_activity_weeklyworkplanfrombp               TO claude_safety_ro;
GRANT SELECT ON safety_activity_weeklyworkplanperskoregion         TO claude_safety_ro;
GRANT SELECT ON safety_activity_weeklyworkplanperskoteam           TO claude_safety_ro;

-- SHE (Safety, Health, Environment) measurement
GRANT SELECT ON she_measurement_sherecord               TO claude_safety_ro;
GRANT SELECT ON she_measurement_shecategory             TO claude_safety_ro;
GRANT SELECT ON she_measurement_sheitemscore            TO claude_safety_ro;

-- Compliance
GRANT SELECT ON compliance_check_checklistrecord        TO claude_safety_ro;
GRANT SELECT ON compliance_check_checklistitem          TO claude_safety_ro;

-- Risk assessment
GRANT SELECT ON committee_workriskassessment            TO claude_safety_ro;

-- Organization lookup (non-PII, structural data)
GRANT SELECT ON sysmanage_region                        TO claude_safety_ro;
GRANT SELECT ON sysmanage_teamregion                    TO claude_safety_ro;
GRANT SELECT ON sysmanage_companymaster                 TO claude_safety_ro;

-- Board (posts, comments — may contain names; evaluate if needed)
GRANT SELECT ON board_post                              TO claude_safety_ro;
GRANT SELECT ON board_comment                           TO claude_safety_ro;
GRANT SELECT ON board_file                              TO claude_safety_ro;

-- EXPLICITLY NOT GRANTED (PII / auth / sessions):
--   auth_user
--   accounts_userprofile
--   accounts_passwordhistory
--   django_session
--   django_admin_log
--   authtoken_token
--   django_content_type
--   django_migrations
--   auth_group
--   auth_group_permissions
--   auth_permission
--   auth_user_groups
--   auth_user_user_permissions


-- =============================================================================
-- 3B. TANGO DB — claude_tango_ro (standard level)
--     Approved tables: alarm data + Opark daily reports. No embeddings.
-- =============================================================================

-- Schema access
GRANT CONNECT ON DATABASE postgres TO claude_tango_ro;
GRANT USAGE ON SCHEMA public TO claude_tango_ro;

-- Core alarm tables
GRANT SELECT ON alarm_data              TO claude_tango_ro;
GRANT SELECT ON alarm_events            TO claude_tango_ro;
GRANT SELECT ON alarm_history           TO claude_tango_ro;
GRANT SELECT ON alarm_hourly_summary    TO claude_tango_ro;
GRANT SELECT ON alarm_raw_logs          TO claude_tango_ro;
GRANT SELECT ON facility_info           TO claude_tango_ro;

-- Views
GRANT SELECT ON alarm_statistics        TO claude_tango_ro;

-- Opark daily report (operational data)
GRANT SELECT ON opark_daily_report      TO claude_tango_ro;

-- EXPLICITLY NOT GRANTED at standard level:
--   report_embeddings           (vector data, large, not needed for standard queries)
--   report_ontology             (classification tree — grant if needed)
--   report_alarm_matches        (ML matching results — grant if needed)
--   opark_b2bequipmaster        (equipment master — grant if needed)
--   opark_cmsequipmaster
--   opark_equipmaster
--   opark_evchrgequipmaster
--   opark_fronthaulequipmaster


-- #############################################################################
-- SECTION 4: GRANT — "full" level
-- #############################################################################
--
-- "full" users get SELECT on all operational tables.
-- Still NO access to PII tables or bedrock_platform.
--

-- =============================================================================
-- 4A. SAFETY DB — claude_safety_full_ro (full level)
-- =============================================================================

GRANT CONNECT ON DATABASE safety TO claude_safety_full_ro;
GRANT USAGE ON SCHEMA public TO claude_safety_full_ro;

-- Grant SELECT on ALL tables first, then revoke the blacklist.
-- This is cleaner than listing 40+ tables individually.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO claude_safety_full_ro;

-- Now revoke the PII / auth blacklist
REVOKE SELECT ON auth_user                      FROM claude_safety_full_ro;
REVOKE SELECT ON accounts_userprofile           FROM claude_safety_full_ro;
REVOKE SELECT ON accounts_passwordhistory       FROM claude_safety_full_ro;
REVOKE SELECT ON django_session                 FROM claude_safety_full_ro;
REVOKE SELECT ON django_admin_log               FROM claude_safety_full_ro;
-- Revoke Django internals (not useful, reduces attack surface)
REVOKE SELECT ON django_content_type            FROM claude_safety_full_ro;
REVOKE SELECT ON django_migrations              FROM claude_safety_full_ro;
REVOKE SELECT ON auth_group                     FROM claude_safety_full_ro;
REVOKE SELECT ON auth_group_permissions         FROM claude_safety_full_ro;
REVOKE SELECT ON auth_permission                FROM claude_safety_full_ro;
REVOKE SELECT ON auth_user_groups               FROM claude_safety_full_ro;
REVOKE SELECT ON auth_user_user_permissions     FROM claude_safety_full_ro;
-- Revoke DRF token table if it exists (safe to run even if table doesn't exist
-- — wrap in DO block to avoid error)
DO $$
BEGIN
    EXECUTE 'REVOKE SELECT ON authtoken_token FROM claude_safety_full_ro';
EXCEPTION WHEN undefined_table THEN
    NULL;  -- table doesn't exist, nothing to revoke
END $$;

-- IMPORTANT: Do NOT add default privileges for full level.
-- New tables require explicit review before granting access.
-- This prevents accidental exposure if someone adds a "users_backup" table.


-- =============================================================================
-- 4B. TANGO DB — claude_readonly (full level, existing user)
-- =============================================================================

GRANT CONNECT ON DATABASE postgres TO claude_readonly;
GRANT USAGE ON SCHEMA public TO claude_readonly;

-- Grant SELECT on all current tables in the postgres database
GRANT SELECT ON ALL TABLES IN SCHEMA public TO claude_readonly;

-- No blacklist needed on aiagentdb/postgres — no PII tables exist there.
-- But do NOT add ALTER DEFAULT PRIVILEGES. New tables need explicit review.


-- #############################################################################
-- SECTION 5: ROW-LEVEL SECURITY (Future consideration)
-- #############################################################################
--
-- Currently not implemented. Documented for Phase 2+ consideration.
--
-- If region-based filtering is needed (e.g., user sees only their region):
--
--   ALTER TABLE safety_activity_workinfo ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY region_filter ON safety_activity_workinfo
--       FOR SELECT
--       USING (region_sko = current_setting('app.user_region', true));
--
-- The Pod's psql wrapper would SET app.user_region before queries.
-- This requires ALTER TABLE + policy per table — significant effort.
-- Defer until multi-tenant isolation is a hard requirement.
--


-- #############################################################################
-- SECTION 6: VERIFICATION QUERIES
-- #############################################################################
--
-- Run these after applying grants to verify correctness.
--

-- 6A. List all privileges for each role (run on each database)
-- SELECT grantee, table_schema, table_name, privilege_type
-- FROM information_schema.table_privileges
-- WHERE grantee IN ('claude_safety_ro', 'claude_safety_full_ro',
--                    'claude_tango_ro', 'claude_readonly')
-- ORDER BY grantee, table_name;

-- 6B. Confirm PII tables are blocked (should return 0 rows)
-- SELECT grantee, table_name, privilege_type
-- FROM information_schema.table_privileges
-- WHERE grantee IN ('claude_safety_ro', 'claude_safety_full_ro')
--   AND table_name IN ('auth_user', 'accounts_userprofile',
--                       'accounts_passwordhistory', 'django_session')
-- ORDER BY grantee, table_name;

-- 6C. Confirm bedrock_platform is unreachable
-- (Run connected to bedrock_platform)
-- SELECT datname, has_database_privilege('claude_readonly', datname, 'CONNECT')
-- FROM pg_database
-- WHERE datname = 'bedrock_platform';
-- Expected: false

-- 6D. Confirm no default privileges remain
-- SELECT * FROM pg_default_acl
-- WHERE defaclrole IN (
--     SELECT oid FROM pg_roles
--     WHERE rolname IN ('claude_readonly', 'claude_tango_ro',
--                        'claude_safety_ro', 'claude_safety_full_ro')
-- );
-- Expected: 0 rows


-- #############################################################################
-- SECTION 7: .pgpass FILE TEMPLATES
-- #############################################################################
--
-- .pgpass format: hostname:port:database:username:password
-- File must be chmod 600.
--
-- These templates go into entrypoint.sh, replacing the current hardcoded
-- single-line .pgpass.
--

-- ---------------------------------------------------------------------------
-- 7A. "basic" level — NO .pgpass needed
--     No database access at all. Claude Code uses only pre-built skills/reports.
--     The entrypoint.sh should NOT create .pgpass or psql-* scripts.
--     DATABASE_URL and TANGO_DATABASE_URL env vars should NOT be set.
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- 7B. "standard" level — Two separate credentials, limited tables
-- ---------------------------------------------------------------------------
--
-- .pgpass contents (two lines):
--
--   safety-prod-db-readonly.XXXX.ap-northeast-2.rds.amazonaws.com:5432:safety:claude_safety_ro:CHANGE_ME_SafetyStd2026
--   aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com:5432:postgres:claude_tango_ro:CHANGE_ME_TangoStd2026
--
-- Corresponding DATABASE_URL:
--   postgresql://claude_safety_ro:CHANGE_ME_SafetyStd2026@safety-prod-db-readonly.XXXX.ap-northeast-2.rds.amazonaws.com:5432/safety?sslmode=require
--
-- Corresponding psql-tango wrapper connects as claude_tango_ro (not claude_readonly).
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- 7C. "full" level — Two credentials, broad table access (minus PII)
-- ---------------------------------------------------------------------------
--
-- .pgpass contents (two lines):
--
--   safety-prod-db-readonly.XXXX.ap-northeast-2.rds.amazonaws.com:5432:safety:claude_safety_full_ro:CHANGE_ME_SafetyFull2026
--   aiagentdb.cbe68e22if9p.ap-northeast-2.rds.amazonaws.com:5432:postgres:claude_readonly:CHANGE_ME_TangoFull2026
--
-- Corresponding DATABASE_URL:
--   postgresql://claude_safety_full_ro:CHANGE_ME_SafetyFull2026@safety-prod-db-readonly.XXXX.ap-northeast-2.rds.amazonaws.com:5432/safety?sslmode=require
--
-- Corresponding psql-tango wrapper connects as claude_readonly.
-- ---------------------------------------------------------------------------


-- #############################################################################
-- SECTION 8: ENTRYPOINT.SH INTEGRATION PLAN
-- #############################################################################
--
-- The entrypoint.sh must be modified to accept a DB_ACCESS_LEVEL env var
-- (injected by k8s_service.py based on user's approved level).
--
-- Pseudocode for entrypoint.sh:
--
--   DB_ACCESS_LEVEL="${DB_ACCESS_LEVEL:-basic}"
--
--   case "$DB_ACCESS_LEVEL" in
--     basic)
--       # No .pgpass, no psql-* scripts, no DATABASE_URL
--       echo "DB access: none (basic level)"
--       ;;
--     standard)
--       # .pgpass with claude_safety_ro + claude_tango_ro
--       echo "${SAFETY_DB_HOST}:5432:safety:claude_safety_ro:${SAFETY_STD_PASSWORD}" > ~/.pgpass
--       echo "${TANGO_DB_HOST}:5432:postgres:claude_tango_ro:${TANGO_STD_PASSWORD}" >> ~/.pgpass
--       chmod 600 ~/.pgpass
--       # Create psql-safety and psql-tango wrappers with standard credentials
--       ;;
--     full)
--       # .pgpass with claude_safety_full_ro + claude_readonly
--       echo "${SAFETY_DB_HOST}:5432:safety:claude_safety_full_ro:${SAFETY_FULL_PASSWORD}" > ~/.pgpass
--       echo "${TANGO_DB_HOST}:5432:postgres:claude_readonly:${TANGO_FULL_PASSWORD}" >> ~/.pgpass
--       chmod 600 ~/.pgpass
--       # Create psql-safety and psql-tango wrappers with full credentials
--       ;;
--   esac
--
-- k8s_service.py changes:
--   - Add DB_ACCESS_LEVEL env var to Pod spec (from user's approved level)
--   - Add SAFETY_STD_PASSWORD / TANGO_STD_PASSWORD env vars from K8s secrets
--     for standard level users
--   - Add SAFETY_FULL_PASSWORD / TANGO_FULL_PASSWORD env vars from K8s secrets
--     for full level users
--   - Remove hardcoded DATABASE_URL and TANGO_DATABASE_URL for basic level users
--


-- #############################################################################
-- SECTION 9: K8S SECRETS LAYOUT
-- #############################################################################
--
-- New secret structure (replaces the single rds-credentials secret):
--
-- apiVersion: v1
-- kind: Secret
-- metadata:
--   name: rds-credentials-standard
--   namespace: claude-sessions
-- type: Opaque
-- data:
--   safety-user: Y2xhdWRlX3NhZmV0eV9ybw==              # claude_safety_ro
--   safety-password: <base64>
--   safety-host: <base64>
--   tango-user: Y2xhdWRlX3RhbmdvX3Jv                    # claude_tango_ro
--   tango-password: <base64>
--   tango-host: <base64>
--
-- apiVersion: v1
-- kind: Secret
-- metadata:
--   name: rds-credentials-full
--   namespace: claude-sessions
-- type: Opaque
-- data:
--   safety-user: Y2xhdWRlX3NhZmV0eV9mdWxsX3Jv           # claude_safety_full_ro
--   safety-password: <base64>
--   safety-host: <base64>
--   tango-user: Y2xhdWRlX3JlYWRvbmx5                    # claude_readonly
--   tango-password: <base64>
--   tango-host: <base64>
--
-- No secret needed for "basic" level — no DB credentials injected.
--


-- #############################################################################
-- SECTION 10: MIGRATION / ROLLOUT ORDER
-- #############################################################################
--
-- Phase 1: Create roles + set grants (this script)
--   1. Connect to safety-prod-db-readonly, run Section 1A, 2A, 3A, 4A
--   2. Connect to aiagentdb/postgres, run Section 1B, 2B, 3B, 4B
--   3. Connect to aiagentdb/bedrock_platform, run Section 2C
--   4. Run verification queries (Section 6) on both instances
--
-- Phase 2: Update K8s secrets
--   1. Create rds-credentials-standard secret
--   2. Create rds-credentials-full secret
--   3. Keep old rds-credentials secret until all Pods are rotated
--
-- Phase 3: Update application code
--   1. Add db_access_level field to platform users table
--   2. Update k8s_service.py to inject DB_ACCESS_LEVEL + correct secret refs
--   3. Update entrypoint.sh with the level-based branching logic
--   4. Update container-image config/CLAUDE.md to document level differences
--
-- Phase 4: Rotate existing Pods
--   1. Delete all existing Pods (they will reconnect with new credentials)
--   2. Verify each level works with a test login
--
-- Phase 5: Cleanup
--   1. Change all passwords from CHANGE_ME_* to strong generated values
--   2. Delete old rds-credentials secret
--   3. Rotate claude_readonly password (currently hardcoded as TangoReadOnly2026)
--   4. Remove hardcoded password from entrypoint.sh line 82 and 150
--


-- #############################################################################
-- SECTION 11: SECURITY NOTES
-- #############################################################################
--
-- 1. PASSWORD MANAGEMENT
--    All CHANGE_ME_* passwords in this document are placeholders.
--    Generate strong passwords (32+ chars, alphanumeric + symbols) and store
--    them in AWS Secrets Manager, referenced by K8s ExternalSecret or
--    SecretProviderClass (CSI driver).
--
-- 2. CONNECTION LIMITS
--    Each role has CONNECTION LIMIT set. This prevents a compromised Pod
--    from exhausting the RDS connection pool. Adjust limits based on
--    expected concurrent Pods per level:
--      - standard: 10 connections (covers ~10 concurrent standard Pods)
--      - full: 10-15 connections (covers ~5 concurrent full Pods)
--    RDS max_connections is typically 100-200 for db.t3.medium/m5.large.
--
-- 3. NO ALTER DEFAULT PRIVILEGES
--    Intentionally absent. Every new table requires an explicit GRANT.
--    This is a conscious trade-off: more manual work on schema changes,
--    but zero risk of accidental exposure.
--
-- 4. NO WRITE ACCESS
--    No INSERT, UPDATE, DELETE, TRUNCATE, or DDL grants anywhere.
--    Pod users can only SELECT. Even if Claude Code generates a
--    CREATE TABLE or INSERT, PostgreSQL will reject it.
--
-- 5. NETWORK LAYER
--    This design covers PostgreSQL-level access control only.
--    Network-level isolation (Security Groups, NACLs) should also ensure
--    that only the EKS cluster VPC can reach the RDS instances.
--    The bedrock_platform database should additionally have its own
--    Security Group restricting access to only the auth-gateway Pod.
--
-- 6. AUDIT LOGGING
--    Consider enabling pgaudit on both RDS instances to log all
--    SELECT queries by claude_* roles. This provides a forensic trail
--    if a user queries data they shouldn't conceptually access (even if
--    PostgreSQL allows it at the table level).
--
--    ALTER SYSTEM SET pgaudit.log = 'read';
--    ALTER SYSTEM SET pgaudit.role = 'claude_base_ro';
--    -- Requires RDS parameter group change + instance reboot.
--
-- =============================================================================
-- END OF DESIGN
-- =============================================================================
