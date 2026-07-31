-- Migration 056 / PR-MON-M1-STATE-MACHINE-1:
-- bind Monitoring clearance review requests to their exact source status.
--
-- The additive field records the canonical alert status observed while the
-- request-creation path holds the alert row lock. Approval re-locks the alert
-- first, locks the request second, and refuses unless this value still matches.
--
-- Historical rows deliberately remain NULL. Their source status cannot be
-- reconstructed without guessing, so they fail closed at approval and may
-- still be rejected/recreated by an authorised operator. No alert, request,
-- evidence, or audit row is rewritten by this migration.
ALTER TABLE monitoring_alert_review_requests
    ADD COLUMN IF NOT EXISTS source_alert_status TEXT;

-- Narrow rollback (operator-run only, after confirming no pending request
-- depends on the source-state binding):
--
--   ALTER TABLE monitoring_alert_review_requests
--       DROP COLUMN IF EXISTS source_alert_status;
--
-- The migration intentionally does not touch the existing partial unique
-- index uq_monitoring_review_requests_one_pending.
