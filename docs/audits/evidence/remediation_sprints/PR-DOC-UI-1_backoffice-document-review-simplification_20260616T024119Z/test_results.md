# Test Results

Focused post-copy regression run:

```text
pytest arie-backend/tests/test_upload_latency_contracts.py arie-backend/tests/test_pr_doc_ui1_backoffice_document_review.py -q
```

Result:

```text
13 passed in 2.88s
```

Broader targeted regression run:

```text
pytest arie-backend/tests/test_upload_latency_contracts.py arie-backend/tests/test_pr_doc_ui1_backoffice_document_review.py arie-backend/tests/test_pr_doc2a_agent1_evidence_control.py arie-backend/tests/test_doc_policy_canonical_registry.py arie-backend/tests/test_pr6_async_verification_foundation.py -q
```

Result:

```text
42 passed in 4.65s
```

Coverage included:

- Portal upload succeeds without writing portal actor into `documents.uploaded_by`.
- Portal upload creates verification job and returns pending status.
- Portal upload does not run full verification inline.
- Persisted verification checks become available after async worker execution.
- Back-office upload still stores valid officer `uploaded_by`.
- Application Review groups documents by Action required / Missing / Verified / Optional-additional.
- Portal-slot documents render expected type rather than `Unclassified`.
- View/Download are visible for uploaded documents.
- Missing documents disable View/Download.
- Technical/audit fields remain in Details, not default rows.
- PR-DOC2A and canonical policy registry regressions remain covered.

