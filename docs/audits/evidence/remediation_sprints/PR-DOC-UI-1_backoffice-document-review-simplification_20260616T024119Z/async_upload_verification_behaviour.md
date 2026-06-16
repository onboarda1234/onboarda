# Async Upload Verification Behavior

Required behavior:

1. Portal user uploads a document.
2. Backend persists file/document record and returns `201` quickly.
3. Backend queues Agent 1 verification where supported.
4. Portal shows pending/running status while verification completes.
5. Persisted verification checks/results become available after worker execution.

Implemented behavior:

- `DocumentUploadHandler.post` persists the document with `verification_status = pending`.
- For client portal uploads, the handler calls `enqueue_verification_job(...)` after document/audit insertion and before commit.
- The upload response includes:
  - `verification_status`
  - `verification_state`
  - `verification_status_label`
  - `verification_status_tone`
  - `verification_success`
  - `verification_terminal`
  - `verification_queued`
- Portal uses the upload response to render pending state and continues polling `/verification-status`.
- `/api/documents/:id/verify` remains available for explicit verification and is not converted into an async enqueue path in this PR.

Failure behavior:

- If enqueue fails, the upload is still persisted.
- The document metadata records `verification_queue_unavailable`.
- The document remains review-required rather than silently relying on unverified evidence.

