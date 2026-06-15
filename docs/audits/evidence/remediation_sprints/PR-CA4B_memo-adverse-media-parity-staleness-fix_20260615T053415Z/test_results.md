# PR-CA4B Targeted Test Results

Runtime: Python 3.11.15 (`/opt/homebrew/bin/python3.11`)

## Mechanical Checks

- `git diff --check` — PASS
- `python3.11 -m py_compile arie-backend/server.py arie-backend/tests/test_memo_staleness_hard_gate.py arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py` — PASS

## Focused Memo / API Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_memo_staleness_hard_gate.py arie-backend/tests/test_phase3_memo_integrity.py arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py -q
```

Result:

- `40 passed in 2.60s`

Coverage added:

- current CA adverse-media truth marks old no-media memo stale,
- stale trigger is persisted with before/after audit state,
- regenerated memo consumes DB-backed CA adverse-media snapshot,
- application detail API returns `memo_is_stale=true`,
- application detail API returns `memo_requires_regeneration=true`,
- memo metadata cannot remain reliance-ready when current CA adverse-media truth disagrees.

## PR-CA4 / CA Rollup Regression Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_screening_queue.py arie-backend/tests/test_complyadvantage_runtime_e2e.py arie-backend/tests/test_backoffice_ca_truthflow_static.py -q
```

Result:

- `55 passed in 1.65s`

## Closed-Control Regression Subset

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_pr1_client_api_boundary.py arie-backend/tests/test_pr1b_client_notification_boundary.py arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py arie-backend/tests/test_pr5_memo_governance.py arie-backend/tests/test_pr5b_memo_concision.py arie-backend/tests/test_pr6_idv_webhook_runtime_baseline.py arie-backend/tests/test_document_reliance_gate.py arie-backend/tests/test_complyadvantage_config.py arie-backend/tests/test_complyadvantage_payloads.py arie-backend/tests/test_complyadvantage_evidence_audit.py arie-backend/tests/test_screening_queue_state_integrity.py -q
```

Result:

- `99 passed in 7.34s`

## PR-CA2 / PR-CA3 Evidence And Webhook Regressions

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_complyadvantage_webhook_storage.py arie-backend/tests/test_complyadvantage_webhook_handler.py arie-backend/tests/test_complyadvantage_webhook_fetch.py arie-backend/tests/test_complyadvantage_webhook_integration.py arie-backend/tests/test_complyadvantage_evidence_backfill.py -q
```

Result:

- `63 passed in 0.61s`

## Notes

CodeRabbit review follow-up converted the new CA4B application-detail regression to a standalone pytest async HTTP test and corrected the PR-CA4B evidence status wording. The focused memo/API suite above was rerun after that follow-up.

The system `/usr/bin/python3` is Python 3.9.6 and cannot import existing repo code using Python 3.10+ union type syntax. The repo requires Python `>=3.11`, and GitHub CI uses Python 3.11, so branch validation used `/opt/homebrew/bin/python3.11`.
