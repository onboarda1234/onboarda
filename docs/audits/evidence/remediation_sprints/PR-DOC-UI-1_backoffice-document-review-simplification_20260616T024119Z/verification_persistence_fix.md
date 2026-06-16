# Verification Persistence Fix

Symptom:

Portal-uploaded documents could show:

```text
No persisted verification checks are stored for this document yet.
```

Root cause:

- Portal upload returned a newly persisted document before full Agent 1 verification results existed.
- The portal previously attempted to call synchronous `/verify` after upload.
- When checks were not yet persisted, the UI exposed an internal diagnostic-style empty-check message.

Implemented fix:

- Portal upload no longer blocks on synchronous verification.
- Client portal uploads enqueue a verification job during the upload transaction.
- Upload response returns a controlled pending verification payload.
- Added `/api/documents/:id/verification-status` for authoritative persisted status polling.
- Portal renders controlled states:
  - `Upload received - verification pending.`
  - `Verification is running. This status will update automatically.`
  - `Verification details are not available. Officer review may be required.`
- The raw `No persisted verification checks...` message is no longer shown as normal user-facing copy.

Regression coverage:

- `test_upload_does_not_run_full_verification_inline`
- `test_portal_upload_persists_checks_after_async_verification_completes`
- `test_upload_201_response_document_row_and_audit_shape`

