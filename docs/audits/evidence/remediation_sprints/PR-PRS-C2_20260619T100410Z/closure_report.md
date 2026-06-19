# PR-PRS-C2 — Closure Report

**Title:** Memo-gated periodic-review completion (fail-closed quarantine + recovery)
**PR:** #543 · **Merge SHA:** `cda0cc77967c052f0bab90081701123d4119cda5` · **Branch merged to:** `main`
**Date:** 2026-06-19 · **Classification:** P0 (final P0 in the periodic-review remediation series)

## 1. What changed (behaviour)

C2 makes the periodic-review memo a **hard completion gate**:

- A review reaches `completed` **only once its mandatory memo exists**.
- New non-terminal state **`completion_pending_memo`** holds reviews whose outcome
  is recorded but whose memo has not yet been generated.
- **Elevate now, defer cycle:** on quarantine, C1 canonical-risk elevation is applied
  immediately (fail-closed safety), while next-cycle scheduling and PR-01 closure are
  deferred until the memo exists.
- **Hybrid recovery:** inline retries at completion → quarantine on exhaustion →
  env-gated background sweep + manual `POST /api/monitoring/reviews/:id/memo/recover`.
- Loud health alert after `MEMO_QUARANTINE_ALERT_THRESHOLD` cumulative failures.
- P1-MEMO2 memo staleness surfaced on the memo read endpoint.

## 2. Why (pre-C2 gap)

Previously the handler wrote `status='completed'`, applied risk elevation, scheduled
the next cycle, and committed — **then** attempted the memo best-effort, swallowing
failures. A review could therefore be formally closed and its next cycle scheduled
while its mandatory compliance memo never existed. C2 closes that silent gap.

## 3. Evidence summary

| Required evidence | Status |
|---|---|
| Full merge SHA for #543 | ✅ `cda0cc779…` |
| `/api/version` matches deployed SHA | ⏳ Operator probe pending (SHA-pinned image deployed + health verified) |
| Forced memo failure → `completion_pending_memo` | ✅ TEST |
| Review not `completed` while memo missing | ✅ TEST |
| Canonical risk elevation still applies | ✅ TEST |
| No next cycle while quarantined | ✅ TEST |
| Manual `/memo/recover` finalises | ✅ TEST |
| Next cycle only after memo succeeds | ✅ TEST |
| Background sweep tested/simulated | ✅ TEST |
| Active query includes quarantined review | ✅ CODE (`periodic_review_management.py:331`) |
| Terminal/completed filter excludes quarantined | ✅ CODE (`periodic_review_projection_service.py:30`) |
| Audit events present | ✅ CODE + exercised by integration tests |
| Closure report | ✅ This document |

Full row-by-row proof: `evidence_matrix.md`. Test + CI detail: `test_results.md`.
Staging probe steps + expected values: `api_smoke.md`.

## 4. Test status

- Focused suite: **98 passed** (`logs/local_test_run.txt`).
- Post-merge `Onboarda CI` (run `27816794118`, SHA `cda0cc779…`): **success**, including the ≥3800-test full-suite gate and coverage threshold.
- `Deploy to Staging` (run `27816794109`): **success**; SHA-pinned image deployed to ECS; health + portal/backoffice verified.

## 5. Residual / follow-ups

- **Live staging probe** (`api_smoke.md` §1–§4) — the one OPERATOR item; behaviour
  already proven deterministically in-suite, probe confirms it against the deployed SHA.
- **PR-PRS-D** — officer-facing UX for the new state (quarantine badge, staleness
  banner, retry-memo button wired to `/memo/recover`, queue visibility). No logic changes.
- **PR-PRS-E** — audit/notification hardening around the new events + health alert.
- Optional: add dedicated unit tests asserting active-query inclusion (row 10) and
  terminal-filter exclusion (row 11) by name, to upgrade them from CODE to TEST.

## 6. Verdict

PR-PRS-C2 is **MERGED and PROVEN** at the code/test/CI/deploy level. The only open item
is the live staging runtime probe, which is operator-gated and does not change the proven
behaviour. With C2 merged, the periodic-review **P0 line is clear**; remaining work
(PR-PRS-D, PR-PRS-E) is P1 polish/hardening.

**Recommended ledger entry:** `PR-PRS-C2 / #543 — MERGED / CI+DEPLOY GREEN / EVIDENCE PACK COMPLETE (live staging probe pending)`.
