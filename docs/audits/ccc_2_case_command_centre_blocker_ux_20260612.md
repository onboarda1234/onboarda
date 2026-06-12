# CCC-2 Case Command Centre Blocker UX Simplification

Date: 2026-06-12
Branch: `codex/ccc-2-case-command-centre-blocker-ux`

## Summary

CCC-2 replaces the prior one-card-per-blocker Case Command Centre with compact grouped action rows. Officers now see a short summary, grouped mandatory blockers, separated secondary guidance, and deterministic action buttons that route to the relevant resolution surface for the current application.

Final verdict: **PASS**

## SHA Record

| Item | SHA |
| --- | --- |
| `origin/main` SHA | `7f94117f5e8f75e9e3c4dce435b2bef6656523d5` |
| Branch base SHA | `7f94117f5e8f75e9e3c4dce435b2bef6656523d5` |
| Local HEAD SHA during validation | `7f94117f5e8f75e9e3c4dce435b2bef6656523d5` plus working tree changes |
| Local `/api/version` SHA | `unknown` |
| Deployed/staging `/api/version` SHA | Not validated in this local run |

Local `/api/version` returned:

```json
{"git_sha":"unknown","git_sha_short":"unknown","build_time":"unknown","image_tag":"unknown","environment":"testing","service":"regmind-backend"}
```

## Before / After

Before:
- Memo issues rendered as separate cards: stale, not approved, validation pending/failed, supervisor pending/inconsistent, not generated.
- IDV descriptions could expose raw fragments such as `provider=...`, `review_answer=...`, and `source=derived`.
- Secondary guidance could visually compete with blockers.
- Next Best Action repeated a generic CTA rather than the first control to resolve.
- Action buttons depended on per-card tab/anchor strings and were hard to audit.

After:
- Mandatory blockers are grouped by workflow: Screening, Identity Verification, Documents & Evidence, EDD / Investigation, Memo Package.
- Periodic Review and monitoring-owner alerts remain guidance unless marked blocking.
- Memo and supervisor issues collapse into one Memo Package row with expandable details.
- IDV blockers group by affected people and route to the Individual Identity Verification panel.
- The summary uses “Decision stage” and “Activation status” to avoid “Approved / Blocked” contradiction.
- Each grouped row has an explicit `action_key` and action target.

## Source Map

| Source | Backend field/action | Frontend group | Severity | Current wording issue | New grouped wording |
| --- | --- | --- | --- | --- | --- |
| Approval gate | `collect_approval_gate_blockers()` | Derived from `blocker_group` / `action_key` | Mandatory | Multiple raw gate cards | Compact grouped rows |
| Screening | `screening_missing`, `screening_truth`, stale timestamp blockers | Screening | Mandatory | Repeated screening cards | “Screening needs attention” |
| Sumsub IDV | `build_idv_gate_summary()` blockers | Identity Verification | Mandatory | `provider=`, `review_answer=`, `source=derived` | “N people need IDV attention” |
| Documents | `computeDocumentReadinessSummary()` | Documents & Evidence | Mandatory | Separate document/evidence routes | One documents/evidence workflow row |
| Enhanced evidence | `getEnhancedReviewSummary()` | Documents & Evidence | Mandatory | Separate enhanced card | Detail inside Documents & Evidence |
| EDD | lifecycle EDD items / status | EDD / Investigation | Mandatory | Could route to generic alert surface | Current app EDD case/queue context |
| Memo | latest memo status and backend memo gate blockers | Memo Package | Mandatory | Split memo cards and enum wording | One Memo Package row with details |
| Supervisor | supervisor verdict/rerun state | Memo Package | Mandatory | `supervisor_status` enum wording | “Supervisor review needs attention” |
| Periodic Review | active lifecycle review item | Periodic Review | Guidance | Could appear as blocker-like card | Secondary guidance only |

## Compact Layout

- Replaced card grid with dense `details` rows.
- First mandatory blocker group expands by default; other groups remain collapsed.
- Desktop fixture with 9 raw items rendered as 6 grouped rows.
- Mobile fixture rendered 3 grouped rows with `overflow: 0`.
- Summary row includes decision stage, activation status, risk, officer, blocker count, and Next Best Action.

## Action Routing Validation

| Button | Expected target | Actual target | Result | Evidence |
| --- | --- | --- | --- | --- |
| Resolve screening | Application Review screening tab | `detail-tab-screening` active | PASS | Playwright click |
| Review IDV | KYC Docs / `sumsub-idv-panel` | `detail-tab-kyc-docs` active | PASS | Playwright click |
| Review documents | KYC Documents panel | `detail-tab-kyc-docs` active | PASS | Playwright click |
| Open EDD | Current app EDD queue/case context | `openEDDQueueForApplication("ccc2-fixture-app","ARF-CCC2-FIXTURE")` | PASS | Playwright click |
| Open memo | Memo section | `detail-tab-overview` active | PASS | Playwright click |
| Open review | Lifecycle / Periodic Review tab | `detail-tab-lifecycle` active | PASS | Playwright click |
| Approve | Decision modal | Modal opens | PASS | Browser smoke |
| Reject | Decision modal | Modal opens | PASS | Browser smoke |
| More Info | RMI modal | Modal opens | PASS | Browser smoke |
| Officer Correction | Officer correction modal | Modal opens | PASS | Browser smoke |
| Override | Controlled override modal | Modal opens | PASS | Browser smoke |
| Escalate | EDD decision flow | Decision flow opens without submit | PASS | Browser smoke |
| Reassign | Reassignment modal | Modal opens | PASS | Browser smoke |
| Export Pack | Export pack modal | Modal opens | PASS | Browser smoke |

## Browser Evidence

Desktop fixture:
- Grouped rows: 6
- First group: `screening.resolve`
- Raw text scan: no `provider=`, `review_answer=`, `source=derived`, or `OpenSanctions`
- Console errors: none
- Page errors: none
- Failed requests: none
- Screenshot: `docs/audits/ccc2_case_command_centre_browser_20260612.png`

Mobile fixture:
- Grouped rows: 3
- First group: Screening
- Horizontal overflow: 0
- Console errors: none
- Screenshot: `docs/audits/ccc2_case_command_centre_mobile_20260612.png`

## Tests Run

```text
python3 -m py_compile server.py security_hardening.py base_handler.py rule_engine.py screening_state.py sumsub_idv_status.py
PASS

pytest -q tests/test_case_command_centre_runtime.py
26 passed

pytest -q tests/test_api.py -k "approval or blocker or command or idv or memo or screening"
35 passed, 102 deselected

pytest -q tests/test_backoffice_monitoring_navigation_static.py tests/test_backoffice_ca_truthflow_static.py tests/test_screening_queue_state_integrity.py tests/test_kyc_1a_sumsub_idv_visibility.py tests/test_idv_approval_gate.py
73 passed
```

Notes:
- The local app must be run with Python 3.11. macOS `/usr/bin/python3` is 3.9.6 and cannot start the app because other modules use `str | None`.
- WeasyPrint native library warnings appeared during local server startup but did not affect Case Command Centre validation.
- The in-app Browser connector tool was not exposed in this session, so browser validation used local Playwright against `http://127.0.0.1:18080/backoffice`.

## Remaining Gaps

- Staging deployment was not performed in this local sprint, so deployed `/api/version` SHA is not available.
- Local `/api/version` reports `unknown` because build metadata is not injected into the local testing server.
- Screenshots use synthetic fixture data injected into the local page to avoid mutating real application data.

## Final Verdict

**PASS**: blockers are grouped, wording is officer-readable, vertical footprint is materially reduced, and browser validation confirms grouped rendering plus deterministic action routing without console, page, or network failures.
