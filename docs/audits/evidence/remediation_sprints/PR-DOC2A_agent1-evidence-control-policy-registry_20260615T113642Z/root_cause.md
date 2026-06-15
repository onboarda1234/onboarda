# Root Cause

## UI Root Cause

The document verification renderer treated check output as a flat list. Routine successful technical controls and material verification findings shared the same primary visual level, so officers had to scan through non-actionable green passes before seeing whether a document could be relied on.

## Settings Root Cause

The Agent 1 settings page was configured around editable onboarding checks rather than a lifecycle-wide document policy model. There was no policy registry-style inventory showing the evidence families, lifecycle stage, gate behavior, material checks, technical checks, manual acceptance posture, re-screening trigger, and policy ID/version.

## Data Compatibility Root Cause

The canonical `documents` table schema did not include `uploaded_by`, and existing database migrations did not add it. Once Application Review started surfacing `Uploaded by`, the API needed a safe schema path for existing databases. The fix adds:

- `uploaded_by TEXT REFERENCES users(id)` to fresh SQLite/Postgres document schemas.
- `_ensure_document_upload_audit_schema`.
- Migration `v2.42`.
- Regression coverage proving initialized SQLite schemas include the column.

## Reliance-State Root Cause

During browser smoke, a fixture with a material warning produced a correct material panel status of `Review required`, but the dominant card badge initially showed `Verified` because `verification_success` was considered before warning checks. The fix gives material warning/failure signals precedence over `verification_success`.
