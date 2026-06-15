# Targeted Test Results

Raw log: `test_results.raw.log`

## Passed

- PR-DOC2A/static suites: `10 passed`
- Terminology/document reliance UI regression suites: `54 passed`
- Upload/reliance gate suites: `37 passed`
- Memo/approval regression suites: `72 passed`

## Commands

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_backoffice_inline_script_static.py \
  arie-backend/tests/test_pr_doc2a_agent1_evidence_control.py -q

/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_enhanced_requirement_settings.py::test_pr6f_unified_kyc_documents_and_verification_cleanup_are_wired \
  arie-backend/tests/test_ex11_ai_advisory_labels.py \
  arie-backend/tests/test_document_reliance_ui_static.py \
  arie-backend/tests/test_portal_remediation.py::test_backoffice_workflow_test_evidence_ui_is_staging_only_and_truthful -q

/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_document_upload_storage.py \
  arie-backend/tests/test_pr9_duplicate_file_hash.py \
  arie-backend/tests/test_pr5_verification_truthfulness.py \
  arie-backend/tests/test_document_reliance_gate.py -q

/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_api.py::TestGovernanceAttemptAudit::test_approval_document_gate_failure_returns_structured_blockers \
  arie-backend/tests/test_pr5b_memo_concision.py \
  arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py \
  arie-backend/tests/test_phase4_verification_hardening.py \
  arie-backend/tests/test_approval_gate.py -q
```

## Notes

The targeted test set covers default visibility of technical checks, renamed labels, policy registry inventory, unknown/unclassified handling, upload/schema auditability, existing document reliance gates, memo consistency, and approval gate regressions.
