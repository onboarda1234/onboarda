# Locked Decisions

## 2026-04-27 Upload Latency Remediation Process

- GitHub `main` is the source of truth.
- The local checkout may be stale and must not be used as implementation truth.
- Step 1 diagnosis is read-only.
- Implementation starts only after decisions are locked.
- One PR must cover one concern.
- Every deployed fix requires staging verification.
- Merges and staging flag flips require explicit authorization.
- Runtime code changes must not touch unrelated code.

## 2026-04-27 Flag Exposure Contract

- Upload-latency flags are backend-only by default.
- Only `FF_SIZE_CAP_CLIENT_REJECT` and `FF_UX_SPLIT_UPLOAD_VERIFY` may be exposed
  to frontend code.
- Tests must protect this allowlist so future backend flags do not leak through
  careless client flag exposure.

## 2026-04-27 Contract Test Scope

Before upload or verify refactors, tests must lock:

- Upload response shape.
- Document DB row creation.
- Audit event shape.
- Size rejection.
- Verify response and persisted result parity.
- GATE-03 duplicate-detection behavior.

## 2026-04-27 Telemetry Gate

Telemetry PRs must include pre-written CloudWatch queries or dashboard material,
not only structured logs. The acceptance criterion is that upload p50/p95 can be
queried immediately once staging traffic exists.

## 2026-04-27 Quick-Win Stabilization

After polling slowdown and client-side size rejection are deployed and enabled,
hold a 48-72 hour staging soak before structural work. During the soak, observe
telemetry, BO refresh behavior, size rejection, audit events, and error rates.

## 2026-04-29 PR4 Backoffice Polling Slowdown

- PR #192 keeps `FF_POLLING_SLOW` backend-only; it is not exposed through
  frontend code.
- Backoffice applications auto-refresh interval is 120 seconds.
- Backoffice stale-data warning threshold is 180 seconds.
- The 1-second freshness display tick remains local UI only and does not perform
  network polling.
- Existing `/api/applications` ETag / `If-None-Match` / `304` behavior remains
  the hardening contract.

## 2026-04-29 Protected File Override Guard

- PR #194 repaired the protected-files guard so the `protected-file-override`
  label can unblock the workflow only when explicit written project-lead approval
  is also present.
- The override label is an audit mechanism, not standalone approval.
