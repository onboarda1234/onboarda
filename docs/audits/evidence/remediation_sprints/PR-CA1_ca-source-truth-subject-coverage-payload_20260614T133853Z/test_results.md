# PR-CA1 Test Results

## Syntax And Whitespace

Command:

```bash
git diff --check
```

Result: passed.

Command:

```bash
/opt/homebrew/bin/python3.11 -m py_compile arie-backend/screening_config.py arie-backend/screening_complyadvantage/payloads.py arie-backend/screening_complyadvantage/adapter.py arie-backend/screening_complyadvantage/normalizer.py arie-backend/screening_models.py arie-backend/screening_routing.py arie-backend/screening_state.py arie-backend/security_hardening.py arie-backend/server.py arie-backend/screening_provider.py arie-backend/screening_adapter_sumsub.py
```

Result: passed.

## Targeted CA / Provider / UI Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_screening_config.py arie-backend/tests/test_complyadvantage_payloads.py arie-backend/tests/test_screening_adapter_complyadvantage.py arie-backend/tests/test_screening_routing.py arie-backend/tests/test_screening_mode.py arie-backend/tests/test_screening_provider.py arie-backend/tests/test_provider_label_policy.py arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_backoffice_review_audit.py::TestPhaseSixComplyAdvantageStatusUI::test_api_status_panel_lists_complyadvantage_with_correct_responsibility arie-backend/tests/test_monitoring_alerts_sprint1_static.py::test_monitoring_alert_detail_renders_compact_provider_evidence_without_fake_links arie-backend/tests/test_phase6_complyadvantage_readiness.py::test_complyadvantage_status_is_not_live_when_unconfigured arie-backend/tests/test_api.py::TestAuthenticatedAccess::test_screening_status_does_not_expose_unused_provider -q
```

Result:

```text
138 passed in 1.97s
```

Coverage included:

- CA Mesh source-of-truth and display naming.
- Sumsub retained as IDV/KYC, not active AML, when CA is configured.
- Unknown/missing provider does not render as CA.
- Truthful fallback/simulation status.
- Intermediaries included in CA screening.
- Missing intermediary subject data recorded as a gap.
- Intermediary gap blocks screening readiness/approval.
- Entity payload includes available jurisdiction, registration number, address, entity type, incorporation date, business activity, and application reference.
- Entity payload omits unavailable fields safely.
- Back-office/static provider labels use ComplyAdvantage Mesh.

## Closed-Remediation Regression Subset

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_pr1_client_api_boundary.py arie-backend/tests/test_auth_stability.py arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py arie-backend/tests/test_pr5_memo_governance.py arie-backend/tests/test_pr5b_memo_concision.py arie-backend/tests/test_pr6_idv_webhook_runtime_baseline.py -q
```

Result:

```text
67 passed in 6.90s
```

## Previously Failing Full-Suite Assertions

After the first full-suite run, stale assertions were updated and rerun:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_backoffice_review_audit.py::TestPhaseSixComplyAdvantageStatusUI::test_api_status_panel_lists_complyadvantage_with_correct_responsibility arie-backend/tests/test_monitoring_alerts_sprint1_static.py::test_monitoring_alert_detail_renders_compact_provider_evidence_without_fake_links arie-backend/tests/test_phase6_complyadvantage_readiness.py::test_complyadvantage_status_is_not_live_when_unconfigured -q
```

Result:

```text
3 passed in 1.56s
```
