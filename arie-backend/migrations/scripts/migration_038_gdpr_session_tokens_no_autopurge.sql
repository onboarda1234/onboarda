-- Migration 038: Stop the daily audit-trail destruction (audit finding B1)
-- =====================================================================
-- The seeded "session_tokens" retention policy shipped with auto_purge=1 while
-- CATEGORY_TABLE_MAP resolved that category to the audit_log table with a 1-day
-- retention. The scheduled GDPR purge deletes every row older than the cutoff
-- with no action/type predicate, so on every daily PeriodicCallback in
-- staging/production it was destroying the entire generic audit trail down to
-- the last 24 hours.
--
-- The code fix (gdpr.py) removes the session_tokens -> audit_log mapping and
-- makes the automatic purge refuse the audit tables outright. This migration
-- additionally repairs already-deployed databases whose seeded policy row still
-- carries auto_purge=1 (INSERT OR IGNORE seeding never updates existing rows).
--
-- SCOPE: data-only, idempotent. No schema change. Re-running is a no-op.

UPDATE data_retention_policies
   SET auto_purge = 0,
       description = 'Expired authentication tokens and session data. 24-hour retention (documentation only; not auto-purged).'
 WHERE data_category = 'session_tokens'
   AND auto_purge <> 0;
