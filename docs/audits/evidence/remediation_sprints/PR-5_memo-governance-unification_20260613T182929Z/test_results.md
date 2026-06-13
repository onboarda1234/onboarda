# PR-5 Targeted Test Results

## Runtime

- Python: `/opt/homebrew/bin/python3.11` (`Python 3.11.15`)
- Pytest: `9.0.2`

## Compile / Diff Checks

Command:

```bash
git diff --check
```

Result: PASS

Command:

```bash
/opt/homebrew/bin/python3.11 -m py_compile arie-backend/server.py arie-backend/security_hardening.py arie-backend/evidence_pack_export.py arie-backend/edd_memo_integration.py arie-backend/memo_governance.py arie-backend/tests/test_pr5_memo_governance.py arie-backend/tests/test_approval_gate.py arie-backend/tests/test_memo_ordering_gate.py arie-backend/tests/test_pass_with_fixes_approval.py arie-backend/tests/test_supervisor_warnings_approval.py arie-backend/tests/test_backoffice_review_audit.py arie-backend/tests/test_api.py arie-backend/tests/test_create_applicant_country_fix.py arie-backend/tests/test_document_versioning.py arie-backend/tests/test_flagged_doc_override.py arie-backend/tests/test_idv_approval_gate.py arie-backend/tests/test_memo_staleness_approval.py arie-backend/tests/test_memo_staleness_hard_gate.py arie-backend/tests/test_phase4_verification_hardening.py arie-backend/tests/test_screening_freshness.py arie-backend/tests/test_screening_mode.py arie-backend/tests/test_sumsub_409_retry.py arie-backend/tests/test_sumsub_aml_fix.py
```

Result: PASS

## Targeted PR-5 + Regression Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest -q tests/test_pr5_memo_governance.py tests/test_memo_ordering_gate.py tests/test_pass_with_fixes_approval.py tests/test_supervisor_warnings_approval.py tests/test_backoffice_review_audit.py::TestDayThreeApprovalBlockerUX::test_memo_validation_panel_has_visible_approval_blockers tests/test_backoffice_review_audit.py::TestDayThreeApprovalBlockerUX::test_pass_with_fixes_approval_reason_is_captured_by_ui tests/test_pr1_client_api_boundary.py tests/test_pr1b_client_notification_boundary.py tests/test_sprint35.py::TestLogout tests/test_pr3_terminal_record_gate_reconciliation.py tests/test_pr4_screening_memo_readiness_metadata.py tests/test_api.py::TestGovernanceAttemptAudit::test_enhanced_requirement_approval_block_is_audited tests/test_api.py::TestGovernanceAttemptAudit::test_first_approval_202_attempt_is_audited tests/test_api.py::TestMonitoringEnrollmentActuation::test_application_approval_enrolls_monitoring_and_periodic_review tests/test_approval_gate.py::test_validate_approval_requires_explicit_validation_pass tests/test_approval_gate.py::test_validate_approval_requires_explicit_supervisor_consistent tests/test_create_applicant_country_fix.py::test_gate5_allows_live_green_person_aml_after_fix tests/test_document_versioning.py::test_approval_gate_excludes_superseded_flagged_documents tests/test_flagged_doc_override.py tests/test_idv_approval_gate.py::test_manual_verified_allows_idv_gate tests/test_idv_approval_gate.py::test_exception_approved_allows_idv_gate_for_high_risk tests/test_memo_staleness_approval.py tests/test_memo_staleness_hard_gate.py tests/test_phase4_verification_hardening.py::TestApprovalGateStaleness::test_fresh_memo_passes tests/test_phase4_verification_hardening.py::TestApprovalGateStaleness::test_screening_before_submission_blocked tests/test_screening_freshness.py tests/test_screening_mode.py::test_approval_gate_rejects_simulated_nested_screening_report tests/test_screening_mode.py::test_gate5_allows_simulated_company_registry_with_live_sumsub tests/test_screening_mode.py::test_gate5_allows_simulated_ip_geolocation_with_live_sumsub tests/test_screening_mode.py::test_gate5_allows_both_enrichment_simulated_simultaneously tests/test_sumsub_409_retry.py::test_gate5_allows_live_green_person_aml_409 tests/test_sumsub_aml_fix.py::test_gate5_allows_live_green_person_aml
```

Result:

```text
152 passed in 9.08s
```

## Browser / Frontend Static Coverage

- `arie-backend/tests/test_pr5_memo_governance.py::test_memo_ui_captures_reason_and_collapses_diagnostics_static`
- `arie-backend/tests/test_backoffice_review_audit.py::TestDayThreeApprovalBlockerUX::test_pass_with_fixes_approval_reason_is_captured_by_ui`

Result: PASS

Direct browser smoke remains required after merge and staging deployment before FSI-005/FSI-006 can be closed.

## Post-Review Hardening Validation

CodeRabbit review surfaced two material hardening points:

- validate the canonical memo selector `columns` argument before SQL interpolation
- use the actual officer sign-off checkbox state in the memo approval payload

Both were addressed on the PR-5 branch. The brittle static UI test slice was also tightened to inspect the full PASS WITH FIXES branch.

Command:

```bash
/opt/homebrew/bin/python3.11 -m py_compile arie-backend/memo_governance.py arie-backend/tests/test_pr5_memo_governance.py arie-backend/tests/test_backoffice_review_audit.py
```

Result: PASS

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest -q tests/test_pr5_memo_governance.py tests/test_backoffice_review_audit.py::TestDayThreeApprovalBlockerUX::test_pass_with_fixes_approval_reason_is_captured_by_ui
```

Result:

```text
8 passed in 0.66s
```

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest -q tests/test_pr5_memo_governance.py tests/test_memo_ordering_gate.py tests/test_pass_with_fixes_approval.py tests/test_supervisor_warnings_approval.py tests/test_backoffice_review_audit.py::TestDayThreeApprovalBlockerUX::test_memo_validation_panel_has_visible_approval_blockers tests/test_backoffice_review_audit.py::TestDayThreeApprovalBlockerUX::test_pass_with_fixes_approval_reason_is_captured_by_ui tests/test_pr1_client_api_boundary.py tests/test_pr1b_client_notification_boundary.py tests/test_sprint35.py::TestLogout tests/test_pr3_terminal_record_gate_reconciliation.py tests/test_pr4_screening_memo_readiness_metadata.py tests/test_api.py tests/test_approval_gate.py tests/test_create_applicant_country_fix.py tests/test_document_versioning.py tests/test_flagged_doc_override.py tests/test_idv_approval_gate.py tests/test_memo_staleness_approval.py tests/test_memo_staleness_hard_gate.py tests/test_phase4_verification_hardening.py tests/test_screening_freshness.py tests/test_screening_mode.py tests/test_sumsub_409_retry.py tests/test_sumsub_aml_fix.py
```

Result:

```text
431 passed in 21.90s
```
