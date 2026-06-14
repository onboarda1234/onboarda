# PR-CA3 Diagnosis

## Scope

PR-CA3 targets:

- CA-002 — Contradictory CA screening states / impossible clear-unresolved combinations.
- CA-007 — Webhook processing / async loss / reconciliation risk.
- CA-009 — Runtime CA E2E test pack missing.
- CA-010 — CA retry/backoff/circuit/fail-closed policy gaps.

## Dependency Gate

- PR-CA1 merge is present on main: `5d664a51fb0d6161095aff88f17a657b5e23cacd`.
- PR-CA2 merge is present on main: `6b6ea16881ae7f93a0eeb4256bb4f205692be757`.
- PR-CA2 corrective follow-up is present on main: `787ce4a26abfbaceaa043011df4e3f961fa4f418`.
- PR-CA2 evidence in the previous closure pack recorded merged-main staging validation and issue closure.

## Base

- Initial `origin/main` SHA used for diagnosis: `787ce4a26abfbaceaa043011df4e3f961fa4f418`.
- Rebased validation `origin/main` SHA after main advanced: `0d6b7353c7d40c5d23845de472c4bbbe2417ea45`.
- Branch: `codex/pr-ca3-ca-state-integrity-webhook-reconciliation-e2e`.

## Re-diagnosis Findings

### CA-002 State Integrity

Verdict before fix: FAIL.

Findings from initial diagnosis `origin/main`:

- `screening_state.py` had no canonical `stale` state in the business truth model.
- Expired `screening_valid_until` was handled by a later approval freshness gate, but queue/detail/memo truth summaries could still project clear/completed states until that later gate ran.
- Queue canonical projection happened before evidence enrichment. Evidence quality added after enrichment could therefore be unavailable/partial while the projected row still appeared as clear.
- Provider evidence rows containing adverse media could be counted as hits, but a contradictory explicit `adverse_media_status=clear` was not surfaced as a named impossible-state flag.
- Partial/unavailable provider evidence had no consistent queue-level blocker unless it also manifested as a hit or provider failure.

### CA-007 Webhook / Async Reconciliation

Verdict before fix: FAIL/PARTIAL.

Findings from current `origin/main`:

- Mesh webhooks were accepted with `202` before an equivalent durable receipt/retry payload was guaranteed.
- `complyadvantage_webhook_deliveries` tracked webhook id/status but did not retain a redacted provider payload, alert identifiers, retry count, or next retry timestamp.
- Duplicate webhook ids were handled, but accepted webhook processing could fail after acknowledgement without enough local data to reconcile from RegMind state alone.
- Reconciliation/backfill for stuck `received`, `retry_pending`, or stale `processing` webhook deliveries was missing.

### CA-009 Runtime E2E Coverage

Verdict before fix: NOT IMPLEMENTED/PARTIAL.

Findings from current `origin/main`:

- Unit coverage existed for CA config, payloads, normalizer/evidence, webhook storage, and queue behavior.
- There was no single safe runtime E2E pack proving no-hit, unresolved hit, adverse-media hit, provider failure, stale, rescreen, duplicate webhook, reconciliation, and approval-gate paths together.

### CA-010 Retry / Fail-Closed

Verdict before fix: PARTIAL.

Findings from current `origin/main`:

- CA OAuth `401` refresh/retry existed.
- Transient CA GET failures such as `429/5xx` did not have a bounded retry/backoff path.
- POST create/screen calls correctly needed to avoid blind retries because they can create duplicate provider workflows.
- Provider error evidence was not consistently projected into queue/detail truth as reliance-blocking `provider_error`/partial evidence.

## Out Of Scope Kept Out

- Deep adverse-media UI/card redesign remains PR-CA4.
- Full Mesh visual parity remains PR-CA4.
- Country-risk governance remains PR-CR.
- Role-matrix/pilot gating remains PR-7.
- Documentation-only lifecycle remediation remains PR-DOC.
