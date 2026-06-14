# PR-CA2 Root Cause

## Root Cause Summary

The CA/Mesh integration normalized provider results for screening decisions, but did not define a single durable evidence contract across normalized reports, queue rows, review context, audit logs, and UI activity. This created traceability gaps even when CA calls succeeded.

## Root Causes By Issue

### CA-003 - CA evidence incomplete or unavailable

Evidence completeness existed as local queue metadata rather than a canonical cross-path contract. Non-complete evidence could be represented as `partial` or `unavailable` without always carrying an explicit `missing_reason` and `next_action`.

### CA-004 - Missing durable per-subject CA audit chain

Screening lifecycle and review events were logged generically. Audit details did not consistently include CA event type, subject reference, provider references, evidence quality, and before/after state.

### CA-011 - Raw/redacted provider response archival policy unclear

There was no explicit CA/Mesh archival policy distinguishing safe provider references and decision evidence from secrets, tokens, webhook signatures, cookies, and raw provider payload fragments.

### CA-UX-004 - Mesh IDs missing from evidence headers/cards

Provider references were available in some stored payloads but not normalized into a stable summary shape that queue/detail/review/API/UI consumers could rely on.

### CA-UX-009 - Audit trail lacks filtered CA/Mesh screening event timeline

The application audit UI classified CA screening events as generic screening/system events and the backend audit endpoint had no CA/Mesh category filter.

## Not The Root Cause

- PR-CA1 provider source of truth was already merged and present on `origin/main`.
- This PR did not find evidence that Sumsub is the active AML source of truth after PR-CA1.
- This PR does not address full Mesh parity or deep adverse-media review UX.
