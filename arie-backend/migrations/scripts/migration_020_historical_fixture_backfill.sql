-- Migration 020: Historical Fixture Backfill
-- ===========================================
--
-- Day 1 data hygiene: mark confirmed historical smoke/QA/Codex/E2E staging
-- applications as fixtures so the canonical fixture filters can hide them from
-- operational surfaces by default.
--
-- SAFETY
-- ------
-- The backfill is intentionally keyed by exact (ref, company_name) pairs rather
-- than by ref alone or broad name regex. Some refs live in the same sequential
-- namespace as real customer rows; matching both fields prevents a future
-- production database with a same-numbered legitimate ref from being hidden.
--
-- IDEMPOTENCY
-- -----------
-- * The UPDATE only touches rows whose is_fixture value is false/null.
-- * The system audit row is self-guarded with NOT EXISTS, so direct
--   re-execution outside schema_version does not duplicate it.

UPDATE applications
SET is_fixture = TRUE,
    updated_at = CURRENT_TIMESTAMP
WHERE COALESCE(is_fixture, FALSE) = FALSE
  AND (
       (ref = 'ARF-2026-900031' AND company_name = 'PHASE4 Closeout Runtime 20260503160321 Ltd')
    OR (ref = 'ARF-2026-900030' AND company_name = 'PHASE4 Closeout Runtime 20260503160217 Ltd')
    OR (ref = 'ARF-2026-900029' AND company_name = 'PHASE2 Postdeploy Validation 20260503T122058Z Ltd')
    OR (ref = 'ARF-2026-900028' AND company_name = 'PHASE2 Diagnosis 20260503T094527Z Ltd')
    OR (ref = 'ARF-2026-900027' AND company_name = 'PHASE1 Memo Truth Smoke 1777801085 Ltd')
    OR (ref = 'ARF-2026-900026' AND company_name = 'PHASE1 Memo Truth Smoke 1777801021 Ltd')
    OR (ref = 'ARF-2026-900025' AND company_name = 'PHASE1 Memo Truth Smoke 1777800962 Ltd')
    OR (ref = 'ARF-2026-900024' AND company_name = 'PHASE1 Memo Truth Smoke 1777800913 Ltd')
    OR (ref = 'ARF-2026-900023' AND company_name = 'PHASE0 Baseline Audit 1777793617 Ltd')
    OR (ref = 'ARF-2026-900022' AND company_name = 'D2 Verify Probe Ltd')
    OR (ref = 'ARF-2026-900021' AND company_name = 'AUDIT May2 Runtime 1777708928 Ltd')
    OR (ref = 'ARF-2026-900020' AND company_name = 'AUDIT May2 Runtime 1777688957 Ltd')
    OR (ref = 'ARF-2026-900019' AND company_name = 'AUDIT Runtime Upload 1777663856 Ltd')
    OR (ref = 'ARF-2026-900018' AND company_name = 'Codex RMI Smoke 1777639540 Ltd')
    OR (ref = 'ARF-2026-900017' AND company_name = 'Codex Phase1C Smoke 1777617157 Ltd')
    OR (ref = 'ARF-2026-900016' AND company_name = 'Codex Resume Smoke 1777617050 Ltd')
    OR (ref = 'ARF-2026-900015' AND company_name = 'E2E Test Corp 1777617014')
    OR (ref = 'ARF-2026-900014' AND company_name = 'QA E2E Test 1 Standard Trading Ltd')
    OR (ref = 'ARF-2026-900013' AND company_name = 'test')
    OR (ref = 'ARF-2026-100470' AND company_name = 'Priority C QA Validation Ltd')
    OR (ref = 'ARF-2026-100469' AND company_name = 'QA Audit Crypto Payments Ltd')
    OR (ref = 'ARF-2026-100468' AND company_name = 'QA Audit MU SME Ltd')
    OR (ref = 'ARF-2026-100451' AND company_name = 'EntityType Validator 31553 Ltd')
    OR (ref = 'ARF-2026-100447' AND company_name = 'Phase2 Validator Delta')
    OR (ref = 'ARF-2026-100446' AND company_name = 'Phase2 Validator Ltd')
    OR (ref = 'ARF-2026-100422' AND company_name = 'Staging E2E Corp')
  );

INSERT INTO audit_log (
    user_id, user_name, user_role,
    action, target, detail, ip_address
)
SELECT
    'system:fixture-backfill',
    'Migration 020 Historical Fixture Backfill',
    'system',
    'Fixture Backfill',
    'migration:020_historical_fixture_backfill',
    '{"migration":"020_historical_fixture_backfill","target_ref_count":26,"scope":"historical smoke/QA/Codex/E2E applications","match":"ref+company_name","idempotent":true}',
    NULL
WHERE EXISTS (
    SELECT 1 FROM applications
    WHERE
           (ref = 'ARF-2026-900031' AND company_name = 'PHASE4 Closeout Runtime 20260503160321 Ltd')
        OR (ref = 'ARF-2026-900030' AND company_name = 'PHASE4 Closeout Runtime 20260503160217 Ltd')
        OR (ref = 'ARF-2026-900029' AND company_name = 'PHASE2 Postdeploy Validation 20260503T122058Z Ltd')
        OR (ref = 'ARF-2026-900028' AND company_name = 'PHASE2 Diagnosis 20260503T094527Z Ltd')
        OR (ref = 'ARF-2026-900027' AND company_name = 'PHASE1 Memo Truth Smoke 1777801085 Ltd')
        OR (ref = 'ARF-2026-900026' AND company_name = 'PHASE1 Memo Truth Smoke 1777801021 Ltd')
        OR (ref = 'ARF-2026-900025' AND company_name = 'PHASE1 Memo Truth Smoke 1777800962 Ltd')
        OR (ref = 'ARF-2026-900024' AND company_name = 'PHASE1 Memo Truth Smoke 1777800913 Ltd')
        OR (ref = 'ARF-2026-900023' AND company_name = 'PHASE0 Baseline Audit 1777793617 Ltd')
        OR (ref = 'ARF-2026-900022' AND company_name = 'D2 Verify Probe Ltd')
        OR (ref = 'ARF-2026-900021' AND company_name = 'AUDIT May2 Runtime 1777708928 Ltd')
        OR (ref = 'ARF-2026-900020' AND company_name = 'AUDIT May2 Runtime 1777688957 Ltd')
        OR (ref = 'ARF-2026-900019' AND company_name = 'AUDIT Runtime Upload 1777663856 Ltd')
        OR (ref = 'ARF-2026-900018' AND company_name = 'Codex RMI Smoke 1777639540 Ltd')
        OR (ref = 'ARF-2026-900017' AND company_name = 'Codex Phase1C Smoke 1777617157 Ltd')
        OR (ref = 'ARF-2026-900016' AND company_name = 'Codex Resume Smoke 1777617050 Ltd')
        OR (ref = 'ARF-2026-900015' AND company_name = 'E2E Test Corp 1777617014')
        OR (ref = 'ARF-2026-900014' AND company_name = 'QA E2E Test 1 Standard Trading Ltd')
        OR (ref = 'ARF-2026-900013' AND company_name = 'test')
        OR (ref = 'ARF-2026-100470' AND company_name = 'Priority C QA Validation Ltd')
        OR (ref = 'ARF-2026-100469' AND company_name = 'QA Audit Crypto Payments Ltd')
        OR (ref = 'ARF-2026-100468' AND company_name = 'QA Audit MU SME Ltd')
        OR (ref = 'ARF-2026-100451' AND company_name = 'EntityType Validator 31553 Ltd')
        OR (ref = 'ARF-2026-100447' AND company_name = 'Phase2 Validator Delta')
        OR (ref = 'ARF-2026-100446' AND company_name = 'Phase2 Validator Ltd')
        OR (ref = 'ARF-2026-100422' AND company_name = 'Staging E2E Corp')
)
AND NOT EXISTS (
    SELECT 1 FROM audit_log
    WHERE action = 'Fixture Backfill'
      AND target = 'migration:020_historical_fixture_backfill'
);
