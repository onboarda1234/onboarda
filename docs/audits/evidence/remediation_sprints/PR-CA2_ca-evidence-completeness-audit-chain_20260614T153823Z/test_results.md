# PR-CA2 Targeted Test Results

## Interpreter

- `/opt/homebrew/bin/python3.11`
- Python: `3.11.15`

## Compile

Command:

```bash
/opt/homebrew/bin/python3.11 -m py_compile screening_complyadvantage/normalizer.py screening_complyadvantage/orchestrator.py screening_complyadvantage/evidence_policy.py server.py
```

Result:

```text
PASS
```

## Focused CA Evidence/Audit Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest tests/test_screening_complyadvantage_normalizer.py tests/test_complyadvantage_evidence_audit.py tests/test_screening_queue.py::test_screening_queue_links_ca_evidence_by_exact_identifiers tests/test_screening_queue.py::test_screening_queue_preserves_nested_provider_references tests/test_screening_review.py::test_screening_review_context_carries_ca_provider_refs_and_evidence_quality tests/test_backoffice_ca_truthflow_static.py::test_backoffice_audit_trail_has_filtered_ca_mesh_timeline tests/test_backoffice_review_audit.py::TestApplicationAuditLogEndpoint::test_frontend_audit_filter_chips_are_available
```

Result:

```text
47 passed in 1.99s
```

## Closed Remediation Regression Subset

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest tests/test_phase6_complyadvantage_readiness.py tests/test_screening_config.py tests/test_provider_label_policy.py tests/test_complyadvantage_payloads.py tests/test_screening_adapter_complyadvantage.py tests/test_screening_mode.py tests/test_pr1_client_api_boundary.py tests/test_auth_stability.py tests/test_pr3_terminal_record_gate_reconciliation.py tests/test_pr4_screening_memo_readiness_metadata.py tests/test_pr5_memo_governance.py tests/test_pr5b_memo_concision.py tests/test_pr6_async_verification_foundation.py tests/test_pr6_idv_webhook_runtime_baseline.py tests/test_pr6_observability_baseline.py
```

Result:

```text
168 passed in 8.92s
```

## Frontend/Static Back-Office Checks

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest tests/test_backoffice_ca_truthflow_static.py tests/test_backoffice_review_audit.py tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_activity_log_filters_and_unknown_fallback_are_safe
```

Result:

```text
124 passed in 3.02s
```

## CI-Equivalent Lint Selector

Command:

```bash
find . -name "*.py" -not -path "./tests/*" -not -path "./.venv/*" | xargs flake8 --count --select=E9,F63,F7,F82 --show-source --statistics
```

Result:

```text
0
```

## Diff Hygiene

Command:

```bash
git diff --check
```

Result:

```text
PASS
```

## Notes

System `python3` is Python 3.9.6 and cannot collect the backend suite because the project requires Python 3.11+ syntax. Validation used Homebrew Python 3.11.15.
