# Targeted Test Results

## Local targeted suite

Command:

```bash
pytest -q \
  arie-backend/tests/test_pr_doc_verify_coverage_ui_1.py \
  arie-backend/tests/test_pr_doc_ui2_manus_compact_document_review.py \
  arie-backend/tests/test_pr_doc_ui1_backoffice_document_review.py \
  arie-backend/tests/test_pr_doc2a_agent1_evidence_control.py
```

Result:

- `35 passed`

## Local regression extension

Command:

```bash
pytest -q \
  arie-backend/tests/test_document_reliance_ui_static.py \
  arie-backend/tests/test_pr_doc_recon1_policy_reconciliation.py \
  arie-backend/tests/test_doc_policy_canonical_registry.py
```

Result:

- `14 passed`

## CI-fix regression subset

Command:

```bash
pytest -q \
  arie-backend/tests/test_enhanced_requirement_settings.py \
  arie-backend/tests/test_pr_doc_verify_coverage_ui_1.py \
  arie-backend/tests/test_pr_doc_ui2_manus_compact_document_review.py \
  arie-backend/tests/test_pr_doc_ui1_backoffice_document_review.py \
  arie-backend/tests/test_pr_doc2a_agent1_evidence_control.py \
  arie-backend/tests/test_document_reliance_ui_static.py \
  arie-backend/tests/test_pr_doc_recon1_policy_reconciliation.py \
  arie-backend/tests/test_doc_policy_canonical_registry.py
```

Result:

- `68 passed`

## EX-11 compatibility regression subset

Command:

```bash
pytest -q \
  arie-backend/tests/test_ex11_ai_advisory_labels.py \
  arie-backend/tests/test_enhanced_requirement_settings.py \
  arie-backend/tests/test_pr_doc_verify_coverage_ui_1.py \
  arie-backend/tests/test_pr_doc_ui2_manus_compact_document_review.py \
  arie-backend/tests/test_pr_doc_ui1_backoffice_document_review.py \
  arie-backend/tests/test_pr_doc2a_agent1_evidence_control.py \
  arie-backend/tests/test_document_reliance_ui_static.py \
  arie-backend/tests/test_pr_doc_recon1_policy_reconciliation.py \
  arie-backend/tests/test_doc_policy_canonical_registry.py
```

Result:

- `118 passed`
