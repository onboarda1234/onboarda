# PR Closure Report

## PR name

`PR-CA3 — CA State Integrity, Webhook Reconciliation and Runtime E2E`

## Linked remediation IDs

- `CA-002`
- `CA-007`
- `CA-009`
- `CA-010`

## Original issue summary

RegMind needed CA/Mesh screening state to be reliable, reconcile-able, fail-closed, and runtime-tested across no-hit, hit, adverse media, failure, stale, rescreen, webhook, and approval-gate paths.

## Re-diagnosis result

- Initial diagnosis `origin/main` SHA: `787ce4a26abfbaceaa043011df4e3f961fa4f418`
- Rebased validation `origin/main` SHA: `0d6b7353c7d40c5d23845de472c4bbbe2417ea45`
- Branch name: `codex/pr-ca3-ca-state-integrity-webhook-reconciliation-e2e`
- Branch commit SHA: recorded in PR metadata/final response after final amend
- Does the issue still exist on current `origin/main`? Yes. See `diagnosis.md`.
- Evidence: local code inspection and test diagnosis from latest `origin/main`.

## Root cause

See `root_cause.md`.

## Files changed

- `arie-backend/db.py`
- `arie-backend/screening_complyadvantage/client.py`
- `arie-backend/screening_complyadvantage/webhook_handler.py`
- `arie-backend/screening_complyadvantage/webhook_storage.py`
- `arie-backend/screening_state.py`
- `arie-backend/security_hardening.py`
- `arie-backend/server.py`
- `arie-backend/tests/test_approval_gate.py`
- `arie-backend/tests/test_complyadvantage_client.py`
- `arie-backend/tests/test_complyadvantage_runtime_e2e.py`
- `arie-backend/tests/test_complyadvantage_webhook_handler.py`
- `arie-backend/tests/test_complyadvantage_webhook_storage.py`
- `arie-backend/tests/test_screening_queue.py`
- `arie-backend/tests/test_screening_state_priority_a.py`

## Behaviour before fix

- Stale CA screening freshness was not a first-class canonical state for queue/detail/gate truth.
- Queue canonical projection could remain clear after evidence enrichment added partial/unavailable provider evidence.
- `adverse_media_status=clear` could coexist without an explicit contradiction flag when adverse-media provider evidence was present.
- Webhook receipts did not preserve enough redacted payload/retry metadata to reconcile accepted-but-not-processed deliveries.
- Safe transient CA GET failures were not retried with bounded backoff.
- No dedicated CA runtime E2E acceptance pack existed.

## Behaviour after fix

- Stale CA screening is canonical `stale`, non-terminal, not defensibly clear, and approval-blocking.
- Partial/unavailable/provider-error evidence is projected as reliance-blocking unless a terminal hit has a properly recorded officer false-positive clearance with review evidence.
- Adverse-media evidence contradicting a clear adverse-media label is explicitly flagged and requires review.
- Webhook receipt is durable before acknowledgement and stores redacted payload, alert identifiers, retry count, and next retry time.
- Retry-pending/stuck webhook deliveries can be reconciled idempotently.
- CA GET requests retry once on safe transient `429/5xx` responses; create/screen POST requests are not blindly retried.
- Runtime E2E fixtures cover no-hit, hit, adverse media, provider failure, stale, rescreen, duplicate webhook, reconciliation, and approval gate paths.

## Tests added/updated

- Added `arie-backend/tests/test_complyadvantage_runtime_e2e.py`.
- Updated stale/evidence integrity tests in `test_screening_state_priority_a.py`.
- Updated queue contradiction tests in `test_screening_queue.py`.
- Updated webhook receipt/reconciliation tests in `test_complyadvantage_webhook_storage.py`.
- Updated webhook handler receipt test in `test_complyadvantage_webhook_handler.py`.
- Updated CA client retry tests in `test_complyadvantage_client.py`.
- Updated approval-gate stale regression in `test_approval_gate.py`.

## Targeted test results

Command:

```bash
PYTHONPATH=arie-backend /opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_screening_freshness.py \
  arie-backend/tests/test_screening_review.py \
  arie-backend/tests/test_complyadvantage_runtime_e2e.py \
  arie-backend/tests/test_screening_state_priority_a.py \
  arie-backend/tests/test_screening_queue.py \
  arie-backend/tests/test_complyadvantage_webhook_storage.py \
  arie-backend/tests/test_complyadvantage_webhook_handler.py \
  arie-backend/tests/test_complyadvantage_client.py \
  arie-backend/tests/test_approval_gate.py \
  -q
```

Result:

```text
199 passed in 2.51s
```

## Full suite results

Command:

```bash
PYTHONPATH=arie-backend /opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
BLOCKED locally by WeasyPrint/Pango CFFI segmentation fault during test collection.
```

See `full_suite_results.md`.

## Browser test results, if applicable

- Browser: pending after staging deploy.
- URL: pending.
- Role: pending.
- Steps: pending.
- Result: pending.
- Screenshot path: pending.

## Staging deploy evidence

- Merged main SHA: pending.
- Deployment mechanism: pending.
- ECS/task/image evidence, if applicable: pending.
- Deployed at: pending.

## /api/version evidence

Endpoint:

```text
https://staging.regmind.co/api/version
```

Result:

```json
{
  "git_sha": "pending",
  "image_tag": "pending"
}
```

Verdict:

- [ ] `git_sha` equals merged main SHA
- [ ] `image_tag` equals merged main SHA

## API smoke test evidence

- Endpoint(s): pending.
- Role/token type: pending.
- Expected: pending.
- Actual: pending.
- Raw evidence path: pending.

## Browser smoke test evidence, if applicable

- URL: pending.
- Role: pending.
- Expected: pending.
- Actual: pending.
- Screenshot path: pending.
- Console/network notes: pending.

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-CA3_ca-state-integrity-webhook-reconciliation-e2e_20260614T175929Z/`

## Remaining risks

- Full-suite evidence depends on GitHub CI due local WeasyPrint/Pango CFFI segfault.
- Staging validation is still required before issue closure.
- Browser smoke remains pending if officer-visible queue/status semantics are treated as UI-affecting.

## Items not closed by this PR

- `CA-002`, `CA-007`, `CA-009`, and `CA-010` remain `PARTIALLY FIXED` until PR merge, staging deployment, `/api/version` match, staging smoke, and closure evidence are complete.
- No PR-CA4, PR-7, DOC, CR, or unrelated remediation item is closed by this PR.

## Final closure verdict

Choose one:

- `PARTIALLY FIXED`

Rationale:

Code and local targeted/regression tests are complete, but staging deployment, `/api/version`, API/runtime smoke, browser smoke if required, and final closure evidence are still pending.
