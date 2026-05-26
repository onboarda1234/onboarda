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

## 2026-05-20 Verification Truthfulness Program Order

- Step 0 changed the remediation order because provider-error-driven outcomes
  were above threshold: `29 / 314 = 9.2%` of current documents and
  `29 / 152 = 19.1%` of flagged current documents.
- The locked order is now `PR5 -> PR8 -> PR6 -> PR7 -> PR9`.
- Do not flip async verification before PR8 reliability remediation.
- No pilot until PR5, PR8, PR6, and PR7 staging soak pass with no false-success
  rendering, coherent `submit-kyc`, acceptable provider-error rate, defined/met
  async SLA, and no screening-provider regression.

## 2026-05-20 PR5 Truthfulness State Model

- Backend-owned verification states are `pending`, `in_progress`, `verified`,
  `flagged`, and `failed`.
- Only `verified` may render success semantics. Portal and back office must
  consume backend-owned verification state metadata instead of inventing local
  success mappings.
- New uploads begin as `pending`; synchronous verification transitions through
  `in_progress` before final `verified|flagged|failed`.
- `submit-kyc` is now a hard gate: required KYC documents must exist and be
  `verified` before submission.
- Document verification transitions and `submit-kyc` attestation/block events
  must remain auditable.
- `FF_POLLING_SLOW` remains backend-only; BO timing is driven through safe
  server-provided runtime config.
