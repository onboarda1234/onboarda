# Test Results

## Static / Compile

- `git diff --check` - pass
- `python3 -m py_compile arie-backend/memo_handler.py arie-backend/pdf_generator.py arie-backend/tests/test_pr5b_memo_concision.py` - pass

## Focused PR-5B Tests

- `pytest -q arie-backend/tests/test_pr5b_memo_concision.py` - 7 passed

Coverage:

- Blocked pending screening produces one authoritative `REVIEW` recommendation.
- `APPROVE_WITH_CONDITIONS` is not present in the default blocked memo.
- Pending screening is not a risk-decreasing factor or mitigant.
- Simple blocked memo is materially shorter and preserves appendix evidence.
- Repeated screening-pending boilerplate is constrained in default content.
- AI explainability is compact and has no default agent pathway.
- Messy officer-note text is sanitized from formal memo output.
- PDF renderer produces a decision-paper view plus appendix evidence index via fake WeasyPrint adapter.

## Memo Governance / PR-5 Regression

- `pytest -q arie-backend/tests/test_pr5b_memo_concision.py arie-backend/tests/test_pr5_memo_governance.py` - 14 passed
- `pytest -q arie-backend/tests/test_memo_staleness_approval.py arie-backend/tests/test_memo_staleness_hard_gate.py arie-backend/tests/test_memo_ordering_gate.py arie-backend/tests/test_decision_path_integrity_priority_b.py` - 60 passed

## Screening / FSI-007 Regression

- `pytest -q arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py arie-backend/tests/test_screening_state_priority_a.py arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py` - 61 passed

## FSI-001 / FSI-002 Regression

- `pytest -q arie-backend/tests/test_pr1_client_api_boundary.py arie-backend/tests/test_pr1b_client_notification_boundary.py arie-backend/tests/test_auth.py arie-backend/tests/test_auth_extended.py` - 46 passed

## Audit / Export Adjacent Regression

- `pytest -q arie-backend/tests/test_audit_export.py arie-backend/tests/test_audit.py arie-backend/tests/test_phase4_reporting_evidence.py arie-backend/tests/test_audit_before_after.py` - 70 passed

## Phase 3 Memo Integrity

- `pytest -q arie-backend/tests/test_phase3_memo_integrity.py -k 'not authoritative_risk_metadata and not fingerprint'` - 17 passed, 5 deselected

The deselected tests import `server.py`, which imports `evidence_pack_export.py`, which locally triggers the known WeasyPrint/Pango CFFI segfault. This is documented in `full_suite_results.md`.

## Local PDF Test Module

- `pytest -q arie-backend/tests/test_pdf_generator.py` - 8 skipped locally because native WeasyPrint libraries are unavailable.
