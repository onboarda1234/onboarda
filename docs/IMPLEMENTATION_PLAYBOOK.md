# Upload Latency Remediation Implementation Playbook

Status: Active
Scope: staging only, AWS ECS `regmind-staging`, `staging.regmind.co`
Source of truth: GitHub `main`

This playbook governs the upload-latency remediation program. It exists to keep
latency fixes small, observable, reversible, and reviewable while preserving the
current compliance and audit behavior.

## Non-Negotiables

- GitHub `main` is the source of truth. Do not implement from stale local files.
- Staging only. Demo and future production changes require separate review.
- One PR equals one concern. Split schema, behavior, UI, tests, and infra unless
  they are inseparable.
- Risky behavior changes must be feature-flagged and default off.
- Backend-only flags must not leak to frontend responses by default.
- No behavior change may be bundled with a mechanical refactor.
- Contract tests must exist before upload, verify, or BO upload refactors.
- Rolling-deploy-safe migrations only.
- Each deployed fix must be verified on staging before it is considered done.
- Do not hardcode credentials or environment-specific secrets.
- Do not change unrelated code.

## Locked Flag Exposure Rule

Upload-latency flags are server-side by default. Only these flags may be exposed
to frontend code:

- `FF_SIZE_CAP_CLIENT_REJECT`
- `FF_UX_SPLIT_UPLOAD_VERIFY`

All other upload-latency flags must remain backend-only unless a later locked
decision explicitly changes the allowlist.

## Initial PR Sequence

### PR 0: Governance

Add this playbook and `.auto-memory/` records. No runtime code.

Acceptance:
- The process lives on GitHub `main` once merged.
- Locked decisions and PR ledger are initialized.

### PR 1: Flag Foundation

Add upload-latency feature flags, all default off:

- `FF_POLLING_SLOW`
- `FF_SIZE_CAP_CLIENT_REJECT`
- `FF_UX_SPLIT_UPLOAD_VERIFY`
- `FF_UPLOAD_ASYNC`
- `FF_ASYNC_VERIFY`
- `FF_GATE03_INDEXED_DEDUP`
- `FF_PRESIGNED_UPLOAD`

Acceptance:
- All environments define the same flag keys.
- All new flags default off.
- Frontend exposure is exactly the upload-latency allowlist above.
- Backend-only flags do not appear in client flag responses.

### PR 2: Contract Tests

Add or strengthen document upload and verification contract tests before any
refactor.

Acceptance:
- Upload `201` response body shape is locked.
- Document DB row creation is locked.
- Upload audit event shape is locked.
- Size rejection behavior is locked.
- Verify response and persisted verification result parity are locked.
- GATE-03 duplicate-detection behavior is locked.

### PR 3: Telemetry And Queries

Add structured timing logs and pre-written CloudWatch Log Insights queries.

Acceptance:
- Upload timing markers cover auth, body extraction, local write, S3 put, DB
  insert, and total request duration.
- Verify timing markers cover DB loads, hash loop, AI/Claude verification,
  persistence, and total request duration.
- A reviewer can open a saved/pre-written CloudWatch query and see upload
  p50/p95 immediately after deployment traffic exists.

### PR 4: Polling Quick Win

Gate BO polling slowdown behind `FF_POLLING_SLOW`.

Acceptance:
- Flag off preserves current behavior.
- Flag on changes BO polling from 30 seconds to 5 minutes.
- Staging verification confirms BO still refreshes and error rates do not rise.

### PR 5: Size Cap Quick Win

Gate BO client-side 10 MB rejection behind `FF_SIZE_CAP_CLIENT_REJECT`.

Acceptance:
- Flag off preserves current 25 MB client behavior.
- Flag on rejects files over 10 MB before upload starts.
- Staging verification proves an over-limit file does not reach the backend.

### PR 5.5: Stabilization Window

After PR 4 and PR 5 are deployed and both flags are enabled, hold a 48-72 hour
staging soak with no new risky merges.

Acceptance:
- Upload telemetry is observed.
- BO refresh expectations still hold.
- Size rejection works.
- Audit log behavior is intact.
- No error-rate regression is observed.

## Later Gates

Later work must not begin until PR 5.5 passes:

- Split upload and verification UX.
- Offload blocking upload work from the Tornado IOLoop.
- Introduce async verification worker and queue.
- Upgrade RDS before horizontal ECS scaling.
- Scale ECS horizontally.
- Move upload data path to presigned browser-to-S3 uploads.

Each later gate needs its own diagnosis, locked decisions, tests, rollback plan,
review checklist, deployment verification, and `.auto-memory/` update.
