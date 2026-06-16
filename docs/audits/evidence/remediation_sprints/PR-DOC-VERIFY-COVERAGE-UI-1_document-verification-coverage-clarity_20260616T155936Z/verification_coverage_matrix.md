# Verification Coverage Matrix

## UI model introduced in this PR

For each uploaded document in Application Review:

- default row shows:
  - document name
  - expected type / portal slot
  - document status
  - one primary issue
  - one blocker
  - one next action
  - direct actions

- `Details` shows:
  - verification details grid
  - verification coverage summary
  - technical audit details drawer

## Coverage summary fields

- Checks passed
- Checks failed
- Warnings
- Skipped
- Not run
- System-blocked
- Expected checks count
- Persisted checks count
- Missing expected checks list where runtime-executable policy coverage is incomplete

## Coverage classification logic

- `Complete`
  - runtime policy exists
  - persisted checks exist
  - expected checks are covered
  - no system-access failure

- `Incomplete`
  - runtime policy exists
  - persisted checks do not cover expected checks
  - or policy mapping is missing

- `Pending`
  - verification state is pending/running and checks are not yet persisted

- `System issue`
  - file-access/runtime failure prevents reliable analysis

- `Manual review only`
  - policy is non-runtime/manual-review-only
  - automated runtime checks are not expected
