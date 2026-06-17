# PR-KYC-DOCS-ACTION-STABILITY-1 Closure Report

Status: In progress

Base `origin/main` SHA: `53352ce74c04202de96a2e24beb4b72e300ae604`

Branch: `codex/pr-kyc-docs-action-stability-1`

## Local Implementation

- Backend rejects `rejected` document review actions when the reason/comment is empty or whitespace-only.
- UI rejection modal requires a rejection reason before submit and no longer labels rejection as optional.
- Reject/review, Re-Verify, and back-office upload refresh paths preserve the KYC Documents / Documents & Evidence tab.
- View uses the existing `?view=inline` path and opens in a new tab; Download stays as the attachment path.
- S3 presigned URLs now receive the intended `inline` or `attachment` content disposition without changing document authorization.
- Row-level More dropdown closes on outside click, Escape, and after selecting an action.

## Local Tests

- `/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_api.py::TestAuthenticatedAccess::test_document_reject_requires_non_empty_reason_without_mutation arie-backend/tests/test_api.py::TestAuthenticatedAccess::test_document_reject_valid_reason_persists_and_audits_context arie-backend/tests/test_document_download_safety.py::TestDocumentDownloadSafety::test_s3_preview_requests_inline_disposition_and_download_requests_attachment arie-backend/tests/test_kyc_docs_action_stability_static.py arie-backend/tests/test_pr_doc_verify_coverage_ui_1.py arie-backend/tests/test_pr_doc_ui1_backoffice_document_review.py arie-backend/tests/test_pr_doc_ui2_manus_compact_document_review.py -q`
  - Result: `36 passed`
- `/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_document_download_safety.py arie-backend/tests/test_document_upload_storage.py arie-backend/tests/test_doc_policy_canonical_registry.py -q`
  - Result: `35 passed`
- `/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_rmi_requests.py -q`
  - Result: `5 passed`
- `git diff --check`
  - Result: passed

## Pending Closure Items

- PR opened and CI passed.
- PR merged to main.
- Staging deployed from merged main.
- Authenticated `/api/version.git_sha` and `image_tag` match merge SHA.
- API smoke passes.
- Authenticated browser smoke passes.
- Runtime evidence files updated.

## Residual Issues

None identified locally. Final blocker/non-blocking classification will be updated after staging browser smoke.
