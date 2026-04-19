-- Migration 012: PR-A Lifecycle Data Trust Hardening -- audit-log
-- legacy_unmapped classification of pre-existing monitoring_alerts rows.
-- =====================================================================
--
-- WHY
-- ---
-- PR-A introduces a third lifecycle bucket (``legacy_unmapped``) for
-- monitoring_alerts that are vocabulary-ghosts (state outside the
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
-- The audit entry shape mirrors existing ``lifecycle.*`` entries used
-- by lifecycle_linkage / monitoring_routing (action, target,
-- structured JSON detail, system actor). No new audit format is
-- invented.
--
-- IDEMPOTENCY
-- -----------
-- The migrations runner records this script in ``schema_version`` so
-- it runs at most once per environment. No row is mutated; this is an
-- additive INSERT into audit_log only.
--
-- PORTABILITY
-- -----------
-- Both SQLite and PostgreSQL support standard ``||`` string
-- concatenation and ``CAST(x AS TEXT)``. JSON detail is hand-built
-- via concat for cross-dialect portability rather than relying on
-- json_object() (SQLite) or jsonb_build_object() (PostgreSQL). The
-- output is valid JSON in either dialect; the ``audit_log.detail``
-- column is plain TEXT in both.
--
-- SCOPE
-- -----
-- * Read-only against monitoring_alerts (no UPDATE / DELETE).
-- * INSERT-only against audit_log.
-- * No schema changes (no ALTER, no new column).
-- * No protected file is modified by this migration.
-- * No EX-01..EX-13 control-critical file is touched.

INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail)
SELECT
    'system:lifecycle-quarantine',
    'PR-A Lifecycle Quarantine (migration 012)',
    'system',
    'lifecycle.alert.quarantined',
    'monitoring_alert:' || CAST(id AS TEXT),
    -- Hand-built JSON detail. Reasons array is constructed by joining
    -- the two predicate-positive markers with a comma separator.
    '{"reasons":['
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
    || '],"before_state":{"id":' || CAST(id AS TEXT)
    || ',"status":' ||
        CASE WHEN status IS NULL THEN 'null'
             ELSE '"' || REPLACE(status, '"', '\"') || '"' END
    || ',"application_id":' ||
        CASE WHEN application_id IS NULL THEN 'null'
             ELSE '"' || REPLACE(application_id, '"', '\"') || '"' END
    || ',"linked_periodic_review_id":' ||
        CASE WHEN linked_periodic_review_id IS NULL THEN 'null'
             ELSE CAST(linked_periodic_review_id AS TEXT) END
    || ',"linked_edd_case_id":' ||
        CASE WHEN linked_edd_case_id IS NULL THEN 'null'
             ELSE CAST(linked_edd_case_id AS TEXT) END
    || ',"bucket":"hidden_ghost"}'
    || ',"after_state":{"bucket":"legacy_unmapped"}'
    || ',"migration":"012_legacy_unmapped_audit_classification"}'
FROM monitoring_alerts
WHERE
    -- vocabulary_ghost: status outside canonical AND no downstream linkage
    (
        status NOT IN
            ('open','triaged','assigned','dismissed',
             'routed_to_review','routed_to_edd')
        AND linked_periodic_review_id IS NULL
        AND linked_edd_case_id IS NULL
    )
    -- OR unscopable_no_application: no application binding
    OR application_id IS NULL;
