# PR Closure Report

## PR name

`PR-5 - Memo Governance Unification and Decision UX Cleanup`

## Linked remediation IDs

- `FSI-005`
- `FSI-006`

## Original issue summary

- `FSI-005`: Latest compliance memo selection was inconsistent across backend consumers, UI/API display, supervisor/validation/approval, EDD linkage, and export paths.
- `FSI-006`: Memo approval UI could not submit required approval reason, leaving backend/UI governance out of sync.

## Re-diagnosis result

- Current `origin/main` SHA: `3a79bd014cc84e8189e50daeecc325c3cb9af0a5`
- Branch name: `codex/pr5-memo-governance-unification`
- Branch commit SHA: recorded in PR description and final response.
- Does the issue still exist on current `origin/main`? Yes.
- Evidence: `diagnosis.md`, `root_cause.md`, branch diff, targeted tests.

## Root cause

The product had no canonical memo selector. Consumers selected latest memos through duplicated SQL with inconsistent ordering. The UI also lacked an approval reason input and did not send `approval_reason`, despite memo approval requiring documented rationale for decision defensibility.

## Files changed

- `arie-backend/memo_governance.py`
- `arie-backend/server.py`
- `arie-backend/security_hardening.py`
- `arie-backend/evidence_pack_export.py`
- `arie-backend/edd_memo_integration.py`
- `arie-backoffice.html`
- `arie-backend/tests/test_pr5_memo_governance.py`
- Existing regression fixture/test updates in backend tests.

## Behaviour before fix

- Different memo consumers could select different latest memo rows.
- Approval could operate on a memo different from the displayed/exported memo.
- Back-office UI stated the approval reason could not be captured/submitted.
- `approveMemo()` omitted `approval_reason`.
- Validation FAIL with no issues could display "No issues found".

## Behaviour after fix

- Canonical selector uses deterministic order: `COALESCE(version, 0) DESC, created_at DESC, id DESC`.
- Application detail, validation, supervisor, approval, decision gate, export, PDF, and EDD memo linkage use canonical selection.
- API responses expose selected memo ID and selector metadata where relevant.
- Memo approval requires non-empty `approval_reason`.
- Approval reason is persisted, audited, and included in export/PDF evidence where applicable.
- UI renders a consolidated memo status panel, approval reason input, and collapsed full memo/diagnostics.

## Tests added/updated

- Added `arie-backend/tests/test_pr5_memo_governance.py`.
- Updated memo approval/gate tests for mandatory approval reason.
- Updated fixtures that create approved memos so non-memo-gate tests remain focused.
- Updated back-office static UI tests to assert reason capture rather than the old missing-reason defect.

## Targeted test results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest -q tests/test_pr5_memo_governance.py tests/test_memo_ordering_gate.py tests/test_pass_with_fixes_approval.py tests/test_supervisor_warnings_approval.py tests/test_backoffice_review_audit.py::TestDayThreeApprovalBlockerUX::test_memo_validation_panel_has_visible_approval_blockers tests/test_backoffice_review_audit.py::TestDayThreeApprovalBlockerUX::test_pass_with_fixes_approval_reason_is_captured_by_ui tests/test_pr1_client_api_boundary.py tests/test_pr1b_client_notification_boundary.py tests/test_sprint35.py::TestLogout tests/test_pr3_terminal_record_gate_reconciliation.py tests/test_pr4_screening_memo_readiness_metadata.py tests/test_api.py::TestGovernanceAttemptAudit::test_enhanced_requirement_approval_block_is_audited tests/test_api.py::TestGovernanceAttemptAudit::test_first_approval_202_attempt_is_audited tests/test_api.py::TestMonitoringEnrollmentActuation::test_application_approval_enrolls_monitoring_and_periodic_review tests/test_approval_gate.py::test_validate_approval_requires_explicit_validation_pass tests/test_approval_gate.py::test_validate_approval_requires_explicit_supervisor_consistent tests/test_create_applicant_country_fix.py::test_gate5_allows_live_green_person_aml_after_fix tests/test_document_versioning.py::test_approval_gate_excludes_superseded_flagged_documents tests/test_flagged_doc_override.py tests/test_idv_approval_gate.py::test_manual_verified_allows_idv_gate tests/test_idv_approval_gate.py::test_exception_approved_allows_idv_gate_for_high_risk tests/test_memo_staleness_approval.py tests/test_memo_staleness_hard_gate.py tests/test_phase4_verification_hardening.py::TestApprovalGateStaleness::test_fresh_memo_passes tests/test_phase4_verification_hardening.py::TestApprovalGateStaleness::test_screening_before_submission_blocked tests/test_screening_freshness.py tests/test_screening_mode.py::test_approval_gate_rejects_simulated_nested_screening_report tests/test_screening_mode.py::test_gate5_allows_simulated_company_registry_with_live_sumsub tests/test_screening_mode.py::test_gate5_allows_simulated_ip_geolocation_with_live_sumsub tests/test_screening_mode.py::test_gate5_allows_both_enrichment_simulated_simultaneously tests/test_sumsub_409_retry.py::test_gate5_allows_live_green_person_aml_409 tests/test_sumsub_aml_fix.py::test_gate5_allows_live_green_person_aml
```

Result:

```text
152 passed in 9.08s
```

## Full suite results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest -q
```

Result:

```text
BLOCKED locally by WeasyPrint/Pango CFFI segmentation fault during evidence_pack_export.py import.
```

See `full_suite_results.md`.

## Browser test results, if applicable

Branch-stage browser smoke not run. Static UI tests passed. Staging browser smoke is mandatory after merge before closure.

## Staging deploy evidence

- Merged main SHA: `TBD`
- Deployment mechanism: `TBD`
- ECS/task/image evidence: `TBD`
- Deployed at: `TBD`

## /api/version evidence

Pending post-merge staging deployment.

## API smoke test evidence

Pending post-merge staging deployment.

## Browser smoke test evidence, if applicable

Pending post-merge staging deployment.

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-5_memo-governance-unification_20260613T182929Z/`

## Remaining risks

- Full local backend suite cannot be completed because of native WeasyPrint/Pango CFFI crash.
- Browser smoke remains required after merged-main staging deployment.
- API smoke remains required after merged-main staging deployment.
- GitHub CI must provide authoritative full-suite evidence.

## Items not closed by this PR

- `FSI-005` remains `PARTIALLY FIXED` until merged-main staging API/browser smoke proves closure.
- `FSI-006` remains `PARTIALLY FIXED` until merged-main staging API/browser smoke proves closure.
- No other remediation item is closed by this PR.

## Final closure verdict

`PARTIALLY FIXED`

Rationale:

Branch code, targeted tests, and regression tests pass. Closure still requires PR merge, deployed-main staging `/api/version` alignment, staging API smoke, staging browser smoke, and complete evidence.
