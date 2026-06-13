# Diagnosis

PR: PR-3 — Terminal Record Gate Reconciliation

Remediation ID: FSI-003 — Approved and terminal records fail current approval gates.

Base/source-of-truth SHA: `3f00f491c75a5605440d56899bebb9e513cc1cb3`

Diagnosis date: 2026-06-13

## Source Of Truth

- Confirmed `origin/main` at `3f00f491c75a5605440d56899bebb9e513cc1cb3`.
- Confirmed PR #473 / FSI-002 closure commit is included in latest main.
- Staging `/api/version` matched current main during diagnosis:
  - `git_sha`: `3f00f491c75a5605440d56899bebb9e513cc1cb3`
  - `image_tag`: `3f00f491c75a5605440d56899bebb9e513cc1cb3`

Raw redacted runtime evidence:

- `runtime_json/diagnosis_terminal_gate_staging_redacted.json`

## Runtime Reproduction

Read-only staging diagnosis used an authenticated back-office session and did not trigger provider calls or mutate application state.

Observed:

- `GET /api/applications` returned 332 applications.
- 30 terminal candidates were identified.
- Multiple approved/rejected terminal records returned current-state `gate_blockers` as if they were unresolved approval blockers.

Representative affected records:

- `ARF-2026-900288` — status `approved`, no decision records, current `gate_blocker_count=2`.
- `ARF-2026-900178` — status `approved`, 2 decision records, current `gate_blocker_count=8`.
- `ARF-2026-900235` — status `rejected`, no decision records, current `gate_blocker_count=5`.
- `ARF-2026-PR316-FALSE_POSITIVE-7f1ad7f9` — status `approved`, 2 decision records, current `gate_blocker_count=7`.
- `ARF-2026-MON311-05151513094D77-MEDIUM` — status `approved`, 1 decision record, current `gate_blocker_count=1`.

Examples of misleading current blockers on terminal records:

- unresolved Sumsub IDV
- stale compliance memo
- memo approval/validation/supervisor blockers
- screening freshness/truth blockers

## Diagnosis Result

FSI-003 still existed on latest main/staging before PR-3.

The API presented current approval-gate blockers on approved/rejected historical records without distinguishing:

- decision-time approval basis
- current-state diagnostics
- legacy terminal records with incomplete decision evidence

The back-office Case Command Centre consumed those blockers as authoritative action-required approval blockers, making terminal records appear incorrectly blocked under today's gates.
