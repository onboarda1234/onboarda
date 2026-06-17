# Targeted Local Tests

```bash
pytest -q \
  arie-backend/tests/test_pr_doc_verify_coverage_ui_1.py \
  arie-backend/tests/test_pr_doc_ui2_manus_compact_document_review.py \
  arie-backend/tests/test_pr_doc_ui1_backoffice_document_review.py \
  arie-backend/tests/test_kyc_1a_sumsub_idv_visibility.py \
  arie-backend/tests/test_export_pack_ui_static.py \
  arie-backend/tests/test_application_lifecycle_tab_shell_static.py \
  arie-backend/tests/test_case_command_centre_runtime.py
```

Result: `102 passed`

Additional local regression:

```bash
pytest -q arie-backend/tests/test_ex12_client_security.py
```

Result: `83 passed`
