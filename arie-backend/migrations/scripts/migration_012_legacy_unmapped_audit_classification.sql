-- Migration 012: PR-A Lifecycle Data Trust Hardening -- audit-log
-- legacy_unmapped classification of pre-existing monitoring_alerts rows.
-- =====================================================================
--
-- WHY
-- ---
-- PR-A introduces a third lifecycle bucket (``legacy_unmapped``) for
-- monitoring_alerts that are vocabulary-ghosts (status outside the
-- canonical PR-02 routing vocabulary AND no downstream linkage) or
-- unscopable (application_id IS NULL). The classifier itself is
-- predicate-driven and lives in arie-backend/lifecycle_quarantine.py;
-- it is consulted on every read.
--
-- This migration emits a one-off audit_log entry per pre-existing row
-- that matches the quarantine predicates, so the classification change
-- is recorded with before/after state -- satisfying acceptance
-- criterion 4 in the PR-A brief: "The audit log records the
-- classification change for each affected row with before/after state."
--
-- COLUMN POPULATION PATTERN
-- -------------------------
-- Byte-identical to the canonical lifecycle audit writer
-- (BaseHandler.log_audit, used by lifecycle_linkage._emit_audit):
--
--   user_id      : actor identifier (here a system marker)
--   user_name    : human-readable actor (here a migration marker)
--   user_role    : actor role ('system' for this row's actor)
--   action       : 'lifecycle.alert.quarantined' (mirrors lifecycle.* shape)
--   target       : 'monitoring_alert:<id>'
--   detail       : JSON of CLASSIFICATION METADATA only
--                  ({"classification": ..., "reasons": [...], "migration": ...})
--   ip_address   : NULL -- no request context in a migration; matches
--                  the precedent at server.py:6902 (system-emitted row
--                  that omits ip_address from the column list).
--   before_state : JSON of the alert row's pre-quarantine state
--   after_state  : JSON of the alert row's post-quarantine bucket marker
--
-- The before_state / after_state shape mirrors lifecycle_linkage
-- emitters such as lifecycle.edd.escalated:
--     before_state={...full prior columns...},
--     after_state={"escalated_at": ts}        (single field marker)
-- See arie-backend/lifecycle_linkage.py:331-333. We follow the same
-- "full prior" / "marker" symmetry here.
--
-- IDEMPOTENCY
-- -----------
-- TWO LAYERS:
--   1. The migrations runner records this script in ``schema_version``
--      so it runs at most once per environment by the runner.
--   2. The INSERT itself carries a self-guarded ``WHERE NOT EXISTS``
--      so direct execution by a DBA or a test harness (bypassing
--      schema_version) cannot duplicate audit rows.
--
-- PORTABILITY
-- -----------
-- Both SQLite and PostgreSQL support standard ``||`` string
-- concatenation, ``CAST(x AS TEXT)``, and correlated NOT EXISTS
-- subqueries. JSON detail / before_state / after_state are hand-built
-- via concat for cross-dialect portability rather than relying on
-- json_object() (SQLite) or jsonb_build_object() (PostgreSQL). The
-- output is valid JSON in either dialect; the audit_log JSON columns
-- are plain TEXT in both.
--
-- VOCABULARY DRIFT
-- ----------------
-- The canonical PR-02 vocabulary literals are duplicated below in
-- three IN (...) lists. The Python copy in
-- lifecycle_quarantine.CANONICAL_ALERT_VOCABULARY is parity-tested
-- against monitoring_routing.STATUS_*. The SQL copy here is
-- parity-tested by tests/test_lifecycle_quarantine.py
-- (test_sql_vocabulary_matches_monitoring_routing) which reads this
-- file as text, extracts the literals via regex, and asserts set
-- equality against monitoring_routing.STATUS_*. If you rename a
-- canonical status, BOTH the Python constant AND the three IN (...)
-- lists below MUST be updated together.
--
-- INPUT-ALPHABET ASSUMPTION FOR HAND-ROLLED JSON ESCAPING
-- -------------------------------------------------------
-- The REPLACE chain below escapes ``\`` and ``"`` only, in that order
-- (backslash first, then quote -- order matters; do not reverse).
-- Assumes status and application_id contain only printable ASCII
-- without control characters (\\n, \\t, \\u0000, etc.). monitoring_alerts
-- .status is a slug enum and application_id is a UUID/slug, so this
-- assumption holds for every legitimate value the schema accepts. If
-- a future column with arbitrary user input is serialised here, the
-- escape chain MUST be extended (or rewritten via json_object /
-- jsonb_build_object with a dialect dispatch) before reuse.
--
-- SCOPE
-- -----
-- * Read-only against monitoring_alerts (no UPDATE / DELETE).
-- * INSERT-only against audit_log.
-- * No schema changes (no ALTER, no new column).
-- * No protected file is modified by this migration.
-- * No EX-01..EX-13 control-critical file is touched.

INSERT INTO audit_log (
    user_id, user_name, user_role,
    action, target, detail,
    before_state, after_state
)
SELECT
    'system:lifecycle-quarantine',
    'PR-A Lifecycle Quarantine (migration 012)',
    'system',
    'lifecycle.alert.quarantined',
    'monitoring_alert:' || CAST(id AS TEXT),
    -- detail: classification metadata only (NOT the row's state).
    -- Reasons array constructed by joining predicate-positive markers
    -- with a comma separator.
    '{"classification":"legacy_unmapped","reasons":['
    || CASE
         WHEN status NOT IN
                ('open','triaged','assigned','dismissed',
                 'routed_to_review','routed_to_edd')
              AND linked_periodic_review_id IS NULL
              AND linked_edd_case_id IS NULL
         THEN '"vocabulary_ghost"'
         ELSE ''
       END
    || CASE
         WHEN (status NOT IN
                ('open','triaged','assigned','dismissed',
                 'routed_to_review','routed_to_edd')
               AND linked_periodic_review_id IS NULL
               AND linked_edd_case_id IS NULL)
              AND application_id IS NULL
         THEN ','
         ELSE ''
       END
    || CASE
         WHEN application_id IS NULL
         THEN '"unscopable_no_application"'
         ELSE ''
       END
    || '],"migration":"012_legacy_unmapped_audit_classification"}',
    -- before_state: full prior row identity in its pre-quarantine bucket.
    '{"id":' || CAST(id AS TEXT)
    || ',"status":' ||
        CASE WHEN status IS NULL THEN 'null'
             ELSE '"' || REPLACE(REPLACE(status, '\', '\\'), '"', '\"') || '"' END
    || ',"application_id":' ||
        CASE WHEN application_id IS NULL THEN 'null'
             ELSE '"' || REPLACE(REPLACE(application_id, '\', '\\'), '"', '\"') || '"' END
    || ',"linked_periodic_review_id":' ||
        CASE WHEN linked_periodic_review_id IS NULL THEN 'null'
             ELSE CAST(linked_periodic_review_id AS TEXT) END
    || ',"linked_edd_case_id":' ||
        CASE WHEN linked_edd_case_id IS NULL THEN 'null'
             ELSE CAST(linked_edd_case_id AS TEXT) END
    || ',"bucket":"hidden_ghost"}',
    -- after_state: single-field marker (mirrors the lifecycle.*.* "marker"
    -- symmetry in lifecycle_linkage emitters such as lifecycle.edd.escalated).
    '{"bucket":"legacy_unmapped"}'
FROM monitoring_alerts
WHERE
    (
        -- vocabulary_ghost: status outside canonical AND no downstream linkage
        (
            status NOT IN
                ('open','triaged','assigned','dismissed',
                 'routed_to_review','routed_to_edd')
            AND linked_periodic_review_id IS NULL
            AND linked_edd_case_id IS NULL
        )
        -- OR unscopable_no_application: no application binding
        OR application_id IS NULL
    )
    -- Self-guarded idempotency: do not re-emit if an audit row for
    -- this alert already exists. Defends against direct re-execution
    -- by a DBA or test harness that bypasses schema_version.
    AND NOT EXISTS (
        SELECT 1 FROM audit_log al
        WHERE al.action = 'lifecycle.alert.quarantined'
          AND al.target = 'monitoring_alert:' || CAST(monitoring_alerts.id AS TEXT)
    );
