# PR-DOC1 Test Results

## Interpreter

`/Users/Aisha/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`

Python: `3.12.13`

## Diff Hygiene

Command:

```bash
git diff --check
```

Result:

```text
PASS
```

## Compile

Command:

```bash
/Users/Aisha/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile arie-backend/document_reliance_gate.py arie-backend/verification_state.py arie-backend/security_hardening.py arie-backend/server.py arie-backend/verification_worker.py arie-backend/db.py arie-backend/tests/conftest.py arie-backend/tests/test_screening_mode.py arie-backend/tests/test_document_reliance_gate.py arie-backend/tests/test_document_reliance_ui_static.py
```

Result:

```text
PASS
```

## Targeted DOC-001 Tests

Command:

```bash
/Users/Aisha/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest arie-backend/tests/test_document_reliance_gate.py arie-backend/tests/test_flagged_doc_override.py arie-backend/tests/test_portal_remediation.py arie-backend/tests/test_approval_gate.py arie-backend/tests/test_memo_ordering_gate.py arie-backend/tests/test_memo_staleness_hard_gate.py arie-backend/tests/test_document_reliance_ui_static.py -q
```

Result:

```text
86 passed, 7 warnings in 10.84s
```

Coverage included:

- KYC submit pending/failed/skipped/status-only proof gates.
- Memo generation and memo approval document reliance gates.
- Application approval pending/failed/skipped/stale/missing-proof/flagged gates.
- Manual acceptance positive and negative governance cases.
- Portal/back-office static readiness behavior.
- Existing verified flows and optional non-required document behavior.

## Focused Compatibility Subset

Command:

```bash
/Users/Aisha/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest arie-backend/tests/test_api.py arie-backend/tests/test_case_command_centre_runtime.py arie-backend/tests/test_create_applicant_country_fix.py arie-backend/tests/test_document_versioning.py arie-backend/tests/test_draft_persistence.py arie-backend/tests/test_ex13_batch_refresh.py arie-backend/tests/test_idv_approval_gate.py arie-backend/tests/test_memo_staleness_approval.py arie-backend/tests/test_officer_corrections.py arie-backend/tests/test_pass_with_fixes_approval.py arie-backend/tests/test_phase4_verification_hardening.py arie-backend/tests/test_pr1b_client_notification_boundary.py arie-backend/tests/test_screening_freshness.py arie-backend/tests/test_sumsub_409_retry.py arie-backend/tests/test_sumsub_aml_fix.py arie-backend/tests/test_supervisor_warnings_approval.py -q
```

Result:

```text
473 passed, 31 warnings in 50.61s
```

## Closed-Remediation Regression Subset

Command:

```bash
/Users/Aisha/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest arie-backend/tests/test_phase6_complyadvantage_readiness.py arie-backend/tests/test_screening_config.py arie-backend/tests/test_provider_label_policy.py arie-backend/tests/test_complyadvantage_payloads.py arie-backend/tests/test_screening_adapter_complyadvantage.py arie-backend/tests/test_screening_mode.py arie-backend/tests/test_pr1_client_api_boundary.py arie-backend/tests/test_auth_stability.py arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py arie-backend/tests/test_pr5_memo_governance.py arie-backend/tests/test_pr5b_memo_concision.py arie-backend/tests/test_pr6_async_verification_foundation.py arie-backend/tests/test_pr6_idv_webhook_runtime_baseline.py arie-backend/tests/test_pr6_observability_baseline.py -q
```

Result:

```text
168 passed in 10.73s
```
