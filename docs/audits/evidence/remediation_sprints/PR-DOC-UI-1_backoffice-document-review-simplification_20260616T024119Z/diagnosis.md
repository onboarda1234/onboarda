# PR-DOC-UI-1 Diagnosis

Base source of truth: `origin/main` at `07c992d7716183226d53f70bf0d01bf7e87da874`.

PR-DOC-UI-1 was started after PR-DOC-POLICY-CANONICAL-1. The intended scope is a back-office Application Review document UX simplification, while preserving portal upload and Agent 1 verification behavior.

During PR-DOC-UI-1 testing, portal upload showed two P0 regressions:

1. Client portal upload attempted to write a portal/client subject into `documents.uploaded_by`, which has a foreign key to `users.id`.
2. Portal-uploaded documents could surface the raw message `No persisted verification checks are stored for this document yet.` because the upload response returned a pending document row while verification was not yet persisted.

Current-state diagnosis:

- `documents.uploaded_by` is an officer/user foreign key and must not receive portal client/session identifiers.
- Portal users are represented by the portal auth/application context, not necessarily by a row in `users`.
- Portal upload should remain fast and non-blocking.
- Upload success should persist the document immediately, queue verification where supported, and expose a controlled pending/running/terminal status.
- Back-office Application Review should remain action-first and hide technical/audit internals by default.

Scope boundaries kept:

- No portal upload slot redesign.
- No new verification checks.
- No SAR/STR activation.
- No approval gate weakening.
- No DOC2B/change-management enforcement closure.

