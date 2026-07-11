-- ============================================================================
-- DCI-006 staging remediation — canonicalize legacy/probe off-canon values so
-- Migration v2.47 can install the 3 skipped CHECK constraints on next boot.
--
-- Scope (conditions 2/4/5): ONLY these four values are touched. The enum is NOT
-- widened. supervisor_audit_log is NOT touched. Runs in ONE transaction with a
-- provenance guard that ABORTS (rolling everything back) if any target
-- agent_executions row links to a real/pilot (non-fixture) application.
--
-- Run:  psql "$STAGING_DSN" -v ON_ERROR_STOP=1 -f dci006_staging_remediation.sql
-- (ON_ERROR_STOP=1 makes the RAISE EXCEPTION abort the whole script.)
-- ============================================================================

\echo '================ BEFORE COUNTS (condition 7) ================'
SELECT 'clients.status=disabled'                    AS target, count(*) AS rows FROM clients          WHERE status = 'disabled'
UNION ALL
SELECT 'agent_executions.status=direct_probe'       AS target, count(*) AS rows FROM agent_executions WHERE status = 'direct_probe'
UNION ALL
SELECT 'agent_executions.source=fixture'            AS target, count(*) AS rows FROM agent_executions WHERE source = 'fixture'
UNION ALL
SELECT 'agent_executions.source=staging_direct_probe' AS target, count(*) AS rows FROM agent_executions WHERE source = 'staging_direct_probe'
ORDER BY target;

\echo '================ PROVENANCE PRE-FLIGHT (condition 3) ================'
\echo 'Every target agent_executions row + its linked application. is_fixture MUST be true'
\echo '(or app id LIKE f1xed%). Any FALSE/non-f1xed row is real/pilot evidence -> guard aborts.'
SELECT ae.id          AS agent_exec_id,
       ae.application_id,
       ae.status,
       ae.source,
       a.ref          AS application_ref,
       a.is_fixture,
       (COALESCE(a.is_fixture, false) OR a.id LIKE 'f1xed%') AS treated_as_fixture
FROM   agent_executions ae
JOIN   applications a ON a.id = ae.application_id
WHERE  ae.status = 'direct_probe'
   OR  ae.source IN ('fixture', 'staging_direct_probe')
ORDER BY treated_as_fixture, ae.source, ae.status
LIMIT 50;

BEGIN;

-- Condition 3 guard: abort the transaction if ANY target agent_executions row
-- links to a non-fixture application. Nothing is rewritten in that case —
-- real/pilot evidence must be reviewed by a human, never auto-canonicalized.
DO $$
DECLARE
    real_linked int;
BEGIN
    SELECT count(*) INTO real_linked
    FROM   agent_executions ae
    JOIN   applications a ON a.id = ae.application_id
    WHERE  (ae.status = 'direct_probe'
            OR ae.source IN ('fixture', 'staging_direct_probe'))
      AND  COALESCE(a.is_fixture, false) = false
      AND  a.id NOT LIKE 'f1xed%';
    IF real_linked > 0 THEN
        RAISE EXCEPTION
          'ABORT: % target agent_executions row(s) link to a NON-fixture (real/pilot) application. Not auto-rewriting evidence — review the pre-flight listing.', real_linked;
    END IF;
END $$;

-- (1) clients.status: 'disabled' is already read as inactive-equivalent
--     (db.py:9150, monitoring_automation.py:223 group it with 'inactive').
UPDATE clients
   SET status = 'inactive'
 WHERE status = 'disabled';

-- (2) agent_executions.source: legacy demo-seeder ('fixture') + staging probe
--     ('staging_direct_probe'). source is not used for fixture filtering
--     (that keys off applications.is_fixture), so 'ai' is behaviour-preserving.
UPDATE agent_executions
   SET source = 'ai'
 WHERE source IN ('fixture', 'staging_direct_probe');

-- (3) agent_executions.status: 'direct_probe' is a staging direct-DB probe
--     artifact, not a real agent run — delete it. (Alternative if you prefer to
--     retain the row: UPDATE ... SET status='error' instead of DELETE.)
DELETE FROM agent_executions
 WHERE status = 'direct_probe';

\echo '================ AFTER COUNTS (condition 7) — all must be 0 ================'
SELECT 'clients.status=disabled'                      AS target, count(*) AS rows FROM clients          WHERE status = 'disabled'
UNION ALL
SELECT 'agent_executions.status=direct_probe'         AS target, count(*) AS rows FROM agent_executions WHERE status = 'direct_probe'
UNION ALL
SELECT 'agent_executions.source=fixture'              AS target, count(*) AS rows FROM agent_executions WHERE source = 'fixture'
UNION ALL
SELECT 'agent_executions.source=staging_direct_probe' AS target, count(*) AS rows FROM agent_executions WHERE source = 'staging_direct_probe'
ORDER BY target;

COMMIT;

\echo '================ REMEDIATION COMMITTED — redeploy/restart staging so v2.47 reruns ================'
