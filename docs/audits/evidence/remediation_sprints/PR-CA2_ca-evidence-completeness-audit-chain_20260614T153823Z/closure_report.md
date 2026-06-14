# PR Closure Report

## PR name

`PR-CA2 - CA Evidence Completeness and Audit Chain`

## Linked remediation IDs

- `CA-003`
- `CA-004`
- `CA-011`
- `CA-UX-004`
- `CA-UX-009`

## Original issue summary

CA/Mesh screening evidence was not consistently durable, complete, traceable, or reconstructable at application, subject, hit, and audit-event level.

## Re-diagnosis result

- Current `origin/main` SHA: `5d664a51fb0d6161095aff88f17a657b5e23cacd`
- Branch name: `codex/pr-ca2-ca-evidence-completeness-audit-chain`
- Branch commit SHA: see PR head commit and final response.
- Does the issue still exist on current `origin/main`? Yes, the evidence/audit-chain gaps reproduced by inspection.
- Evidence: `diagnosis.md`

## Root cause

See `root_cause.md`.

## Files changed

- `arie-backend/screening_complyadvantage/normalizer.py`
- `arie-backend/screening_complyadvantage/orchestrator.py`
- `arie-backend/screening_complyadvantage/evidence_policy.py`
- `arie-backend/server.py`
- `arie-backoffice.html`
- `arie-backend/tests/test_screening_complyadvantage_normalizer.py`
- `arie-backend/tests/test_complyadvantage_evidence_audit.py`
- `arie-backend/tests/test_screening_queue.py`
- `arie-backend/tests/test_screening_review.py`
- `arie-backend/tests/test_backoffice_ca_truthflow_static.py`
- `arie-backend/tests/test_backoffice_review_audit.py`
- `docs/screening/complyadvantage/evidence-archival-policy.md`
- `docs/audits/evidence/remediation_sprints/PR-CA2_ca-evidence-completeness-audit-chain_20260614T153823Z/`

## Behaviour before fix

CA/Mesh provider references and evidence quality were not consistently promoted into normalized subject/hit records, queue/detail evidence, screening review audit details, and application audit filtering.

## Behaviour after fix

- Mesh provider references are preserved in normalized provider, subject, hit, queue, review, and audit payloads.
- Evidence quality is canonical and non-complete evidence includes missing reason and next action.
- Screening lifecycle and review audit rows are identifiable as CA/Mesh events and include safe provider references.
- Back-office activity trail exposes a CA/Mesh filter.
- Provider secrets, tokens, cookies, and webhook signatures are redacted.

## Tests added/updated

- `arie-backend/tests/test_screening_complyadvantage_normalizer.py`
- `arie-backend/tests/test_complyadvantage_evidence_audit.py`
- `arie-backend/tests/test_screening_queue.py`
- `arie-backend/tests/test_screening_review.py`
- `arie-backend/tests/test_backoffice_ca_truthflow_static.py`
- `arie-backend/tests/test_backoffice_review_audit.py`

## Targeted test results

See `test_results.md`.

## Full suite results

See `full_suite_results.md`.

## Browser test results, if applicable

See `browser_smoke.md`.

## Staging deploy evidence

See `staging_deploy.md`.

## /api/version evidence

Pending post-merge staging validation.

## API smoke test evidence

See `api_smoke.md`.

## Browser smoke test evidence, if applicable

See `browser_smoke.md`.

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-CA2_ca-evidence-completeness-audit-chain_20260614T153823Z/`

## Remaining risks

- Full Mesh dashboard parity and deep adverse-media display are out of scope for PR-CA2 and remain for PR-CA4.
- Webhook retry/reconciliation hardening remains out of scope for PR-CA3.

## Items not closed by this PR

- `PR-CA3`
- `PR-CA4`
- `PR-7`
- `DOC`
- `CR`

## Final closure verdict

`PARTIALLY FIXED`

Rationale:

Code, targeted tests, regression tests, static checks, and the full local backend suite pass on this branch. Target issues cannot be marked `CLOSED` until PR is merged, deployed to staging, `/api/version` matches merged main SHA, staging API smoke passes, browser smoke passes, and this closure report is completed with post-merge evidence.
