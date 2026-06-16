# Portal Upload FK Fix

Implemented fix:

- Added document upload actor/audit columns to the documents schema.
- Added idempotent schema repair for existing environments.
- Added `_document_upload_actor_metadata(user)`:
  - client portal uploads: `uploaded_by = NULL`, actor metadata populated, `upload_source = client_portal`
  - back-office uploads: `uploaded_by = users.id`, actor metadata populated, `upload_source = back_office_upload`
- Added `_decorate_document_upload_actor(db, doc)` so API responses display safe upload attribution, such as the portal actor display name or `Uploaded by client`, instead of raw internal IDs.

Expected behavior:

- Portal upload succeeds without violating `documents_uploaded_by_fkey`.
- Back-office upload continues to store a valid officer `uploaded_by`.
- Audit/display attribution remains available for both paths.

Regression coverage:

- `test_upload_201_response_document_row_and_audit_shape`
- `test_backoffice_upload_stores_valid_officer_uploaded_by`

