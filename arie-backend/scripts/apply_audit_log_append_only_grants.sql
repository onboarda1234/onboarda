-- P10-7 (RDI-013 non-SAR) — ops half: append-only `audit_log` at the
-- DB-permission layer on staging RDS (PostgreSQL).
--
-- The code half (#837, merged + staging-validated 2026-07-22) added BEFORE
-- UPDATE/DELETE triggers with a maintenance-window bypass for sanctioned
-- retention purges. This pack adds the REDUNDANT permission-layer enforcement
-- the register records as the residual: the application role must not hold
-- UPDATE / DELETE / TRUNCATE on `audit_log`; a separate NOLOGIN maintenance
-- role holds ONLY what the sanctioned purge actually uses — DELETE, never
-- UPDATE or TRUNCATE (via membership grant — NOT an ownership transfer; the
-- app role remains table owner, see boot-safety note).
--
-- No RUNTIME REQUEST PATH performs these operations (enforced + validated by
-- the trigger layer), so applying these grants changes no request-serving
-- workflow. The ONE sanctioned mutation — the manual audit_log retention
-- purge (docs/compliance/MANUAL_PURGE_PROCEDURE.md) — previously ran over the
-- app DSN; AFTER these grants it must run over an ADMIN DSN with
--   SET ROLE <maint_role>;
-- (the #837 trigger maintenance window is a table marker, role-agnostic, so
-- the purge works unchanged under the maintenance role).
--
-- NOTE (boot safety): TRIGGER is deliberately NOT revoked — the app role owns
-- audit_log and db._ensure_audit_log_append_only() re-creates the append-only
-- triggers at EVERY boot; revoking TRIGGER from the owner would fail CREATE
-- TRIGGER and crash-loop the next deploy.
--
-- Usage (run as the RDS master user; NOT via the app):
--   psql "$ADMIN_DATABASE_URL" \
--     -v app_role=regmind_app \
--     -v maint_role=regmind_audit_maint \
--     -v admin_role=<rds_master_user> \
--     -f apply_audit_log_append_only_grants.sql
--
-- Pass the ACTUAL staging role names via -v; `SELECT current_user;` from the
-- app's connection identifies app_role, and over the admin DSN identifies
-- admin_role.

\set ON_ERROR_STOP on

-- 1. Maintenance/trigger-owner role (NOLOGIN; assumed only by admins during a
--    sanctioned maintenance window). Idempotent create.
SELECT format('CREATE ROLE %I NOLOGIN', :'maint_role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'maint_role') \gexec

-- 2. Strip mutating privileges from the app role on audit_log.
--    (TRIGGER intentionally retained — see boot-safety note above.)
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES ON TABLE audit_log FROM :"app_role";

-- 3. Keep exactly what the workflows need: append + read.
GRANT INSERT, SELECT ON TABLE audit_log TO :"app_role";
-- The id sequence must remain usable for INSERT.
GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO :"app_role";

-- 4. Maintenance role: the EXACT privilege set the sanctioned purge uses —
--    nothing more (Codex validation 2026-07-26: the earlier revision granted
--    UPDATE/TRUNCATE on audit_log, SELECT on data_purge_log and SELECT on the
--    sequences, none of which purge_expired_data ever exercises; an
--    append-only design must not hand its maintenance role an UPDATE it can
--    never legitimately run).
--    gdpr.purge_expired_data("audit_logs") runs ENTIRELY over the SET ROLE'd
--    connection, so the maintenance role itself must be able to:
--      * read the retention policy               (data_retention_policies SELECT)
--      * count/MIN/MAX + DELETE-with-WHERE       (audit_log SELECT + DELETE —
--        PostgreSQL requires SELECT for columns referenced in DELETE's WHERE,
--        and the append-only trigger guard SELECTs the window table as the
--        invoking role)
--      * open + close the maintenance-window marker row
--        (audit_maintenance_window INSERT/SELECT/DELETE + sequence USAGE)
--      * write the mandatory evidence row        (data_purge_log INSERT +
--        sequence USAGE; DCI-021: purge without evidence must not commit.
--        NO SELECT: evidence is verified over the admin role, and the
--        maintenance role has no reason to read the log it appends to)
GRANT SELECT, DELETE ON TABLE audit_log TO :"maint_role";
GRANT SELECT ON TABLE data_retention_policies TO :"maint_role";
GRANT SELECT, INSERT, DELETE ON TABLE audit_maintenance_window TO :"maint_role";
GRANT USAGE ON SEQUENCE audit_maintenance_window_id_seq TO :"maint_role";
GRANT INSERT ON TABLE data_purge_log TO :"maint_role";
GRANT USAGE ON SEQUENCE data_purge_log_id_seq TO :"maint_role";

-- 4a. Convergence for a previously-applied revision: if the earlier (broader)
--     pack already ran on this database, strip the surplus so re-running this
--     script always converges to the minimal set. No-ops on a fresh database.
REVOKE UPDATE, TRUNCATE ON TABLE audit_log FROM :"maint_role";
REVOKE SELECT ON TABLE data_purge_log FROM :"maint_role";
REVOKE SELECT ON SEQUENCE audit_maintenance_window_id_seq FROM :"maint_role";
REVOKE SELECT ON SEQUENCE data_purge_log_id_seq FROM :"maint_role";

-- 4b. Make the NOLOGIN maintenance role actually assumable: on PostgreSQL 15
--     (staging RDS) even the master user needs explicit membership to
--     SET ROLE into it.
GRANT :"maint_role" TO :"admin_role";

-- 5. Verification (expect: app f/f/f then t/t; maint_delete/select t;
--    maint_update/truncate/purgelog_select f — the append-only negatives
--    matter as much as the positives).
SELECT
  has_table_privilege(:'app_role',  'audit_log', 'UPDATE')   AS app_update,
  has_table_privilege(:'app_role',  'audit_log', 'DELETE')   AS app_delete,
  has_table_privilege(:'app_role',  'audit_log', 'TRUNCATE') AS app_truncate,
  has_table_privilege(:'app_role',  'audit_log', 'INSERT')   AS app_insert,
  has_table_privilege(:'app_role',  'audit_log', 'SELECT')   AS app_select,
  has_table_privilege(:'maint_role','audit_log', 'UPDATE')   AS maint_update,
  has_table_privilege(:'maint_role','audit_log', 'DELETE')   AS maint_delete,
  has_table_privilege(:'maint_role','audit_log', 'TRUNCATE') AS maint_truncate,
  has_table_privilege(:'maint_role','audit_log', 'SELECT')   AS maint_select,
  has_table_privilege(:'maint_role','data_purge_log', 'SELECT') AS maint_purgelog_select,
  has_table_privilege(:'maint_role','data_retention_policies', 'SELECT') AS maint_policy_select,
  has_table_privilege(:'maint_role','audit_maintenance_window', 'INSERT') AS maint_window_insert,
  has_table_privilege(:'maint_role','audit_maintenance_window', 'DELETE') AS maint_window_delete,
  has_table_privilege(:'maint_role','data_purge_log', 'INSERT') AS maint_evidence_insert,
  has_sequence_privilege(:'maint_role','audit_maintenance_window_id_seq', 'USAGE') AS maint_window_seq,
  has_sequence_privilege(:'maint_role','data_purge_log_id_seq', 'USAGE') AS maint_evidence_seq,
  has_sequence_privilege(:'maint_role','data_purge_log_id_seq', 'SELECT') AS maint_evidence_seq_select;

-- Rollback (emergency only; restores pre-pack state):
--   GRANT UPDATE, DELETE, TRUNCATE ON TABLE audit_log TO <app_role>;
