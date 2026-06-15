# PR-CA4C Targeted Test Results

## Environment

- Worktree: `/tmp/onboarda-pr-ca4c`
- Branch: `codex/pr-ca4c-screening-queue-ux-performance-cleanup`
- Base `origin/main`: `e51dea202171c572261010ea241cb3df186b1288`
- Python: `/opt/homebrew/bin/python3.11`

## Static and Provider Label Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_provider_label_policy.py arie-backend/tests/test_screening_queue.py -q
```

Result:

```text
61 passed in 1.72s
```

Coverage:

- Universal queue search placeholder.
- Removed visible Application reference filter.
- Dynamic `Other person` type filter.
- Lazy-loading full evidence before row detail review.
- Provider/source label policy.
- Summary queue payload without heavy evidence.
- Universal search by subject, company, ARF, and Mesh refs.
- Entity AML pending wording.
- Pagination metadata.

## PR-CA1 / PR-CA2 / PR-CA3 Regression Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_complyadvantage_config.py arie-backend/tests/test_complyadvantage_payloads.py arie-backend/tests/test_complyadvantage_evidence_audit.py arie-backend/tests/test_screening_queue_state_integrity.py -q
```

Result:

```text
51 passed
```

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_complyadvantage_runtime_e2e.py arie-backend/tests/test_complyadvantage_webhook_mapping.py arie-backend/tests/test_complyadvantage_webhook_integration.py -q
```

Result:

```text
14 passed
```

## PR-CA4 / PR-CA4B Regression Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py arie-backend/tests/test_memo_staleness_hard_gate.py arie-backend/tests/test_phase3_memo_integrity.py -q
```

Result:

```text
40 passed
```

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_backoffice_parity.py arie-backend/tests/test_inline_screening_runtime.py arie-backend/tests/test_screening_state_priority_a.py -q
```

Result:

```text
82 passed
```

## Screening Review / Webhook Regression Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_screening_review.py arie-backend/tests/test_complyadvantage_webhook_handler.py arie-backend/tests/test_complyadvantage_webhook_storage.py -q
```

Result:

```text
51 passed
```

## Closed Remediation Regression Subset

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_pr1_client_api_boundary.py arie-backend/tests/test_pr6_idv_webhook_runtime_baseline.py arie-backend/tests/test_document_reliance_gate.py -q
```

Result:

```text
20 passed
```

## Syntax / Diff Checks

Command:

```bash
/opt/homebrew/bin/python3.11 -m py_compile arie-backend/server.py
```

Result:

```text
passed
```

Command:

```bash
git diff --check
```

Result:

```text
passed
```
