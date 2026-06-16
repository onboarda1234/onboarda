# Implementation Summary

Files changed:

- `arie-backend/db.py`
- `arie-backend/server.py`
- `arie-backend/verification_jobs.py`
- `arie-portal.html`
- `arie-backoffice.html`
- `arie-backend/tests/test_upload_latency_contracts.py`
- `arie-backend/tests/test_pr_doc_ui1_backoffice_document_review.py`
- `arie-backend/tests/test_pr6_async_verification_foundation.py`
- `arie-backend/tests/test_pr_doc2a_agent1_evidence_control.py`
- `arie-backend/tests/test_enhanced_requirement_settings.py`
- `arie-backend/tests/test_kyc_1a_sumsub_idv_visibility.py`

Backend:

- Added safe document upload actor metadata.
- Kept `documents.uploaded_by` as officer/user FK.
- Queued portal document verification asynchronously.
- Added document verification status endpoint.
- Returned pending verification payload from portal uploads.

Portal:

- Removed immediate synchronous `/verify` calls after upload.
- Kept upload slot mapping intact.
- Replaced raw missing-check message with controlled pending/running/manual-review wording.

Back office:

- Reworked Application Review document display into action groups.
- Made View/Download visible for all uploaded documents.
- Disabled View/Download only when no file exists.
- Moved technical/audit details behind details disclosure.

Scope not changed:

- No SAR/STR activation.
- No new document verification checks.
- No portal onboarding slot refactor.
- No gate weakening.

