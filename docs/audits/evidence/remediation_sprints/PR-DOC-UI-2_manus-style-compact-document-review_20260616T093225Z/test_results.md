# Test Results

## Focused UI / Policy / Advisory Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_pr_doc_ui2_manus_compact_document_review.py arie-backend/tests/test_pr_doc_ui1_backoffice_document_review.py arie-backend/tests/test_pr_doc2a_agent1_evidence_control.py arie-backend/tests/test_enhanced_requirement_settings.py arie-backend/tests/test_ex11_ai_advisory_labels.py -q
```

Result: `99 passed`

## Canonical Policy / Portal Guard / Approval Gate Regressions

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_pr_doc_recon1_policy_reconciliation.py arie-backend/tests/test_doc_policy_canonical_registry.py arie-backend/tests/test_approval_gate.py arie-backend/tests/test_idv_approval_gate.py arie-backend/tests/test_periodic_review_phase1_canonical.py -q
```

Result: `80 passed`

