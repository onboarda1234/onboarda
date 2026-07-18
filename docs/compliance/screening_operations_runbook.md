# Screening Operations Runbook (RegMind)

Operational knowledge for officers, ops, and agents working the screening surfaces.
Companion to `screening_queue_module_card.md`. Updated 2026-07-17.

## 1. Disposition blast radius (by design — not bugs)
An officer disposition (`POST /api/screening/review`) can trigger, in one transaction:
risk recompute (with screening floors), EDD case creation/routing (a failed EDD route
rolls back and returns 500), memo staleness marking, workflow-state normalisation, and
hash-chained audit entries. A sensitive false-positive clearance parks the subject in
`pending_second_review` and locks other dispositions (honest 409) until a DIFFERENT
SCO/admin confirms. Expect these side effects; do not "fix" them.

## 2. Re-screening — current constraints
* Re-screening an already-screened subject currently errors at Mesh customer-creation
  ("external identifier already assigned"). Since PR #787 this is classified distinctly:
  `degraded_sources` contains `complyadvantage_customer_identifier_conflict`,
  `customer_identifier_conflict: true`, and an explicit overall flag. It is NOT zero hits.
* Risk will NOT decrease off such a report (recompute hold; audit action
  `Risk Recompute Held`).
* The fix (existing-customer re-screen via the stored `customer_identifier` in
  `screening_monitoring_subscriptions`) is SRP-2a — pending Mesh endpoint confirmation.
  NEVER work around the conflict by minting new Mesh customers: webhook routing is by
  customer identifier and duplicates fracture monitoring (ambiguous lookups drop events).

## 3. Governed stale-report refresh harness
`arie-backend/scripts/ops/refresh_stale_screening_reports.py` (SRP-2, PR #786).
Dry-run by default; `--execute` requires `--confirm I-UNDERSTAND-SRP2-CONTROLLED-RESCREEN`.
Rails: archives the outgoing report to `screening_report_archive` BEFORE replacement
(archive-first survives failures); skips any application with officer adjudications;
skips fixtures; refuses production; ≤25/batch, paced; hash-chained audit per refresh;
`--force-refresh` to retry a failed batch; `--base-url` defaults to `http://127.0.0.1:$PORT`
(staging backend listens on 8080). `screening_report_archive` is a regulated table —
append-only, delete-protected.

## 4. Deploy & validation gate discipline (hard-learned)
* `deploy-staging.yml` re-runs FULL CI internally (~60–90+ min); back-to-back merges
  serialise. Gate every validation on the WORKFLOW RUN's terminal state — never wall clock.
* Confirm `/api/version.git_sha` AND `image_tag` equal the merge SHA before validating.
* Codex reviews are a real gate: CI-green does not imply merge (see PR #790 DOB finding).

## 5. Monitoring pipeline caveats
* Alert dedup key is `(provider, case_identifier)` with a partial unique index — alerts
  with NULL `case_identifier` bypass dedup (`document_health_monitor` wrote 123 such
  rows; open follow-up).
* Evidence dedup is `(monitoring_alert_id, evidence_hash)`.
* `screening_monitoring_subscriptions` maps CA `customer_identifier` → application/subject
  and is the authoritative reverse map for webhook routing.

## 6. Fixture governance
QAFIX seeder (`seed_screening_qa_fixtures.py`) creates ARF-QAFIX-001..005 + the inactive
`qafix-client`; refuses production; deletes only under the sanctioned
`fixture_cleanup_nonprod` context. Known limitation: the wipe cannot clear
fixture-linked `edd_cases` created by disposition blast radius (open follow-up).
Re-seeded fixtures reuse the same application ids → their Mesh customers already exist →
re-screens conflict (useful for RESCREEN-1 testing; see §2).

## 7. Performance baselines (staging, 2026-07-16)
Queue default p50 ≈ 0.7s · evidence mode p50 1.096s / p95 1.202s (gzip on; 4.7MB raw →
~15x compressed) · targeted drawer ≈ 0.35–0.55s. Stage timings ship in
`metrics.timings_ms`; a slow wall clock with fast `total_build` points outside the
builder (event-loop wait or network), not at it.
