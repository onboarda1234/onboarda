# PR-CA3 Root Cause

## Root Cause Summary

The CA integration had multiple truthful data components after PR-CA1/PR-CA2, but the state model and async processing path were not yet strict enough to be production-defensible.

## CA-002

Root cause:

- Staleness and evidence quality were treated as peripheral metadata instead of first-class canonical screening states.
- Queue rows were canonicalized before CA evidence enrichment, which allowed post-enrichment evidence gaps to coexist with clear/no-match labels.
- The queue resolver lacked explicit impossible-state detection for adverse-media evidence contradicting "no adverse media" labels.

Impact:

- A stale, partial, or contradictory CA-backed result could be displayed or consumed inconsistently across queue/detail/gate paths.

## CA-007

Root cause:

- Webhook processing was optimized around direct processing and duplicate-id checks, but not around durable pre-ack receipt plus retry/reconciliation.
- The webhook delivery table did not store the redacted payload or retry scheduling data needed to recover a delivery after the handler accepted it.

Impact:

- A webhook could be acknowledged and then lose detail-fetch/update work if runtime processing failed after `202`.

## CA-009

Root cause:

- Test coverage was distributed across unit files without one runtime-style acceptance pack for CA/Mesh no-hit, hit, adverse media, failure, stale, rescreen, webhook replay, reconciliation, and approval gate behavior.

Impact:

- CI could pass unit checks while missing cross-path state integrity regressions.

## CA-010

Root cause:

- The CA API client had authentication retry behavior but not bounded retry/backoff for safe transient GET failures.
- Provider failures and partial evidence were not consistently folded into the canonical reliance-blocking model.

Impact:

- Transient provider failures were less resilient, and provider-error evidence could be less visible to officers/gates than required.
