# Module Card — Screening Queue (RegMind back office)

**Status:** VALIDATED / CHANGE-CONTROLLED (2026-07-17) · frozen pending founder approval for workflow changes
**Owner:** Aisha Sudally (asudally@onboarda.com) — no intentional behaviour change without her explicit approval
**Scope boundary:** this card covers the Screening *Queue* (list surface, `/api/screening/queue`, queue UI, filters, fixture gating, evidence hydration). The Screening *Review* page (per-subject adjudication surface) is a separate, actively-developed workstream (SRP-3/4) and is NOT pilot-ready; the end-to-end screening workflow verdict is gated on it.

## What the module does
Officer-facing triage list of every screened subject (entity / director / UBO / intermediary)
across the newest 200 applications, with canonical screening states, business labels,
filters, pagination, and optional per-row evidence hydration.

## Non-negotiable invariants (all fail-closed, all test-guarded)
1. **Three axes never conflate:** execution state (provider terminal?) · adjudication
   (officer disposition, four-eyes) · provenance (live/sandbox/simulated).
2. **"Clear" requires** terminal execution + live provenance + zero hits.
3. Degraded / pending / errored / identifier-conflict screens are never rendered as
   evidence of zero hits.
4. Fixtures (id namespace `f1xed%`, `is_fixture`, text patterns incl. `%e2e%`, `%smoke%`)
   are excluded from the default officer view; opt-in via `show_fixtures` for
   admin/SCO (+ staging audit-role exception).
5. Four-eyes: sensitive false-positive clearance → pending second review → distinct
   reviewer; queue exposes first reviewer while pending.
6. Scan/evidence caps are reported honestly in `metrics`
   (`application_scan_capped`, `evidence_scan_capped`) — bounded work is visible, never silent.
7. Risk never DECREASES off a non-terminal screening report (recompute hold, PR #787).

## Evidence chain (validations on AWS staging)
| What | Evidence |
|---|---|
| Entity stuck-in-progress root cause + fix | PR #754 (validated) |
| Provenance truth, labels, filters, 7-column layout | PRs #756–#763 (validated; audit PR-A + Phases 2–4) |
| Fixture governance + QA disposition fixtures + seeder (3 staging dialect defects fixed) | PRs #763/#766/#769/#770 — Phase 4 PASS 2026-07-15 |
| Disposition workflows, four-eyes E2E, RBAC (analyst 403 + audit row), 539-row leakage sweep | Phase 5 report PASS 2026-07-15 |
| Evidence-mode latency 21.06s → 1.096s p50 / 1.202s p95 (cap+index+hoist → attribution → gzip) | PRs #773/#778/#781 — Section M closed 2026-07-16 |
| Governed re-screen harness + regulated archive table | PR #786; batch-1 governance rails 10/10 (2026-07-17) |
| Fail-closed risk hold + Mesh identifier-conflict classification | PR #787 (merged; staging demo pending post-seeding) |

## Guard tests (must never regress)
`test_screening_queue.py` · `test_screening_queue_state_integrity.py` ·
`test_seed_screening_qa_fixtures.py` · `test_fixture_exclusion.py` ·
`test_inline_screening_runtime.py` · `test_backoffice_ca_truthflow_static.py` ·
`test_provider_label_policy.py` · `test_declared_pep_truthfulness_priority_a2.py` ·
`test_srp2_refresh_stale_screening_reports.py` · `test_srp2_batch1_regressions.py`

## Known bounds & production gates (NOT pilot blockers)
1. 200-application scan cap — durable fix is application-level pagination (production gate).
2. Production environment (app.regmind.co) not yet provisioned.
3. PostgreSQL-backed test lane for seed/ops tooling (three staging failures were
   SQLite/PG dialect gaps caught only on staging).
4. CloudWatch p95 alarm on `/api/screening/queue` — ops ticket, not yet created.
5. SRP-2a open half: Mesh existing-customer re-screen wiring (blocks re-screens of
   already-screened subjects; periodic 90-day refresh depends on it).

## Change control
Incidental changes to shared code are allowed only if every guard test stays green and
workflow output is unchanged. Intentional behaviour changes require founder approval
recorded in the PR. Deploy validations gate on workflow-run terminal state, never wall
clock (deploy re-runs full CI internally; 60–90+ min is normal).
