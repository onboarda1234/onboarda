# KYC-1A-UX — Sumsub IDV Section B Compact Layout

Date: 2026-06-13

Branch: `codex/kyc-1a-idv-section-b-compact-layout`

## Source Of Truth

- origin/main SHA: `314738bf632454ecd58322c66d15ed02d94889b5`
- branch base SHA: `314738bf632454ecd58322c66d15ed02d94889b5`
- local HEAD SHA before commit: `314738bf632454ecd58322c66d15ed02d94889b5`
- deployed `/api/version` SHA: Not validated yet. PR merge, deploy, and authenticated staging validation remain pending.

## Before Issue

The deployed Application Review Overview still showed the full Sumsub "Individual Identity Verification" workspace inside the Directors & UBOs area. That panel used large person-level cards, consumed too much vertical space, and exposed operational/provider fields by default, including applicant IDs, webhook timestamps, provider outcome details, evidence basis, and evidence-backed flags.

The unmatched Sumsub webhook warning was also too prominent in the normal officer workflow, even though it is an admin/SCO reconciliation concern unless tied to the current application/person.

## User Screenshot Finding

The screenshot finding was confirmed as a UX placement issue: Overview / Directors & UBOs was carrying detailed IDV resolution work that belongs with person identity evidence in KYC & Documents Section B. Overview should keep the officer oriented through CCC only, then route to the detailed workspace.

## Files Changed

- `arie-backoffice.html`
- `arie-backend/tests/test_kyc_1a_sumsub_idv_visibility.py`
- `arie-backend/tests/test_case_command_centre_runtime.py`
- `docs/audits/kyc1a_idv_section_b_browser_20260613/*`

## New Placement

Detailed Sumsub IDV now renders under:

`KYC Documents & Verifications -> B — Directors & UBO Identity Documents -> Individual Identity Verification`

The Overview / Directors & UBOs area now renders the party section only. It no longer mounts the detailed IDV panel or person-level IDV cards there.

## Compact Layout Summary

Section B now uses a compact IDV table/list with default visible fields:

- Person
- Role
- IDV status
- Evidence
- Last update
- Action

Advanced fields are kept behind collapsed row details:

- Applicant ID
- Provider outcome
- Review answer
- Rejection labels
- Applicant created timestamp
- Webhook received timestamp
- Evidence basis
- Evidence-backed flag
- Audit flags
- Manual resolution reason/outcome

Manual resolution remains available through the existing `Resolve IDV Exception` action and modal. The browser check confirms the modal opens without submitting or mutating application data.

## Routing Validation

CCC / Overview `Review IDV` now uses explicit target metadata:

- `target_tab`: `kyc-docs`
- `target_section`: `section-b-identity-verification`
- `scroll_anchor`: `individual-identity-verification`
- `action_mode`: `focus_section`

The routing helper opens collapsed ancestor sections before focusing the target, but it does not expand row-level technical details by default.

Browser result:

| Action | Expected | Actual | Result |
| --- | --- | --- | --- |
| Review IDV | KYC Documents & Verifications, Section B IDV panel | `detail-tab-kyc-docs` visible and `individual-identity-verification` focused | PASS |
| View details | Expand advanced IDV row evidence | Advanced fields visible only after row expansion | PASS |
| Resolve IDV Exception | Open existing manual resolution modal | `modal-idv-resolution` opened | PASS |

## Unmatched Webhook Warning Handling

Unmatched Sumsub webhook events now render as a compact admin/SCO-only notice:

`Admin notice: 25 unmatched Sumsub webhook event(s) need reconciliation.`

The notice is not displayed as a large normal-workflow warning and does not present as a current-application blocker unless surfaced by current application/person IDV rows.

## Tests Run

All commands passed locally.

```text
python3 -m py_compile server.py sumsub_idv_status.py security_hardening.py base_handler.py screening_state.py
PASS
```

```text
pytest -q arie-backend/tests/test_kyc_1a_sumsub_idv_visibility.py arie-backend/tests/test_case_command_centre_runtime.py
43 passed
```

```text
pytest -q arie-backend/tests/test_backoffice_monitoring_navigation_static.py arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_screening_queue_state_integrity.py arie-backend/tests/test_kyc_1a_sumsub_idv_visibility.py arie-backend/tests/test_case_command_centre_runtime.py
94 passed
```

```text
pytest -q arie-backend/tests/test_screening_queue_state_integrity.py arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_backoffice_monitoring_navigation_static.py arie-backend/tests/test_idv_approval_gate.py arie-backend/tests/test_api.py -k "provider or sumsub or complyadvantage or opensanctions or idv"
19 passed, 178 deselected
```

```text
pytest -q arie-backend/tests/test_api.py -k "approval or blocker or command or idv or memo or screening"
35 passed, 103 deselected
```

```text
git diff --check
PASS
```

## Browser Evidence

Local browser validation used the real `arie-backoffice.html` with a synthetic blocked application fixture and stubbed local API responses. No live Sumsub, ComplyAdvantage, or OpenSanctions calls were made.

Evidence directory:

`docs/audits/kyc1a_idv_section_b_browser_20260613/`

Screenshots:

- `overview-no-full-idv.png`
- `section-b-compact-idv.png`
- `expanded-idv-details.png`
- `resolve-idv-modal.png`
- `mobile-section-b-idv.png`
- `browser-results.json`

Browser result summary:

| Check | Result |
| --- | --- |
| Overview full IDV panel absent | PASS |
| CCC compact IDV summary visible | PASS |
| Review IDV routes to Section B | PASS |
| Three compact IDV rows render | PASS |
| Raw/advanced fields hidden by default | PASS |
| Advanced details expandable | PASS |
| Resolve IDV modal opens | PASS |
| Unmatched webhook notice compact/admin-oriented | PASS |
| Mobile compact row layout | PASS |
| Console errors | 0 |
| Page errors | 0 |
| Failed requests | 0 |
| Authenticated/bad HTTP responses | 0 |
| Mutation requests | 0 |

## Provider Label Regression Check

- OpenSanctions was not introduced.
- Sumsub remains labelled as identity verification, not AML/screening.
- ComplyAdvantage/CA remains separate from identity verification.
- CCC IDV text does not expose `provider=`, `review_answer=`, or `source=derived`.

## Remaining Gaps

- PR is not yet opened in this local implementation pass.
- CI has not yet run on GitHub.
- Main has not been merged.
- Staging has not been deployed.
- Authenticated staging validation and `/api/version` SHA verification remain pending.

## Final Verdict

PASS for local implementation.

Do not treat KYC-1A-UX as release-closed until the PR is merged, staging is deployed, `/api/version` matches the deployed main SHA, and authenticated staging browser validation passes.
