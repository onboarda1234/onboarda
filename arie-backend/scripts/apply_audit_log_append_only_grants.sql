-- P10-7 (RDI-013 non-SAR) — ops half: append-only `audit_log` at the
-- DB-permission layer on staging RDS (PostgreSQL).
--
-- The code half (#837, merged + staging-validated 2026-07-22) added BEFORE
-- UPDATE/DELETE triggers with a maintenance-window bypass for sanctioned
-- retention purges. This pack adds the REDUNDANT permission-layer enforcement
-- the register records as the residual: the application role must not hold
-- UPDATE / DELETE / TRUNCATE (or DDL) on `audit_log`; trigger/maintenance
-- ownership moves to a separate role the app cannot assume.
--
-- No application workflow performs these operations (enforced + validated by
-- the trigger layer), so applying these grants changes no workflow behaviour.
--
-- Usage (run as the RDS master user; NOT via the app):
--   psql "$ADMIN_DATABASE_URL" \
--     -v app_role=regmind_app \
--     -v maint_role=regmind_audit_maint \
--     -f apply_audit_log_append_only_grants.sql
--
-- Pass the ACTUAL staging role names via -v; `SELECT current_user;` from the
-- app's connection identifies app_role if unsure.

\set ON_ERROR_STOP on

-- 1. Maintenance/trigger-owner role (NOLOGIN; assumed only by admins during a
--    sanctioned maintenance window). Idempotent create.
SELECT format('CREATE ROLE %I NOLOGIN', :'maint_role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'maint_role') \gexec

-- 2. Strip mutating privileges from the app role on audit_log.
REVOKE UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES ON TABLE audit_log FROM :"app_role";

-- 3. Keep exactly what the workflows need: append + read.
GRANT INSERT, SELECT ON TABLE audit_log TO :"app_role";
-- The id sequence must remain usable for INSERT.
GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO :"app_role";

-- 4. Maintenance role gets the mutating privileges (used only inside the
--    trigger-layer maintenance window during sanctioned purges).
GRANT UPDATE, DELETE, TRUNCATE ON TABLE audit_log TO :"maint_role";

-- 5. Verification (expect: app f/f/f/t/t, maint t/t/t).
SELECT
  has_table_privilege(:'app_role',  'audit_log', 'UPDATE')   AS app_update,
  has_table_privilege(:'app_role',  'audit_log', 'DELETE')   AS app_delete,
  has_table_privilege(:'app_role',  'audit_log', 'TRUNCATE') AS app_truncate,
  has_table_privilege(:'app_role',  'audit_log', 'INSERT')   AS app_insert,
  has_table_privilege(:'app_role',  'audit_log', 'SELECT')   AS app_select,
  has_table_privilege(:'maint_role','audit_log', 'UPDATE')   AS maint_update,
  has_table_privilege(:'maint_role','audit_log', 'DELETE')   AS maint_delete,
  has_table_privilege(:'maint_role','audit_log', 'TRUNCATE') AS maint_truncate;

-- Rollback (emergency only; restores pre-pack state):
--   GRANT UPDATE, DELETE, TRUNCATE ON TABLE audit_log TO <app_role>;
