# ComplyAdvantage Mesh Evidence Archival Policy

## Purpose

RegMind stores enough ComplyAdvantage Mesh evidence to reconstruct AML screening and officer review decisions from RegMind application data, audit logs, and evidence APIs. Officers and auditors must not need CloudWatch logs as the only source of truth for case, subject, or hit-level traceability.

## Stored Evidence

RegMind may store and expose these Mesh evidence fields when available:

- Provider name and display name: `complyadvantage`, `ComplyAdvantage Mesh`
- Mesh case, customer/profile, workflow, alert, risk, match, and profile identifiers
- Subject type, subject key, subject name, and relationship to the application
- Provider decision/status, provider timestamp, `screened_at`, freshness/expiry metadata
- Hit category, match category, source/list/article title, source/publisher, article/source URL, publication date, snippet/summary, and match confidence
- Evidence quality state: `complete`, `partial`, `unavailable`, `stale`, or `provider_error`
- Missing evidence reason and next action for every non-complete evidence state
- Officer disposition, rationale, before/after state, actor, and timestamp

## Redacted Or Excluded Evidence

RegMind must not store or expose provider secrets in application evidence, audit details, UI payloads, test artifacts, screenshots, or reports. The redaction helper removes values for keys containing:

- authorization headers
- bearer/OAuth/access/refresh tokens
- API keys
- passwords and client secrets
- cookies
- webhook signatures

Reference IDs are intentionally preserved because they are audit evidence, not credentials.

## Raw Provider Payload Handling

Raw Mesh payloads are not treated as officer-facing evidence by default. Runtime flows normalize Mesh data into stable RegMind evidence fields and preserve safe provider-specific identifiers. If raw or near-raw provider payload fragments are archived for debugging or audit support, they must be passed through the provider redaction policy first and must not be used as the only source of officer-visible evidence.

## Evidence Quality Contract

Every CA/Mesh subject or hit evidence payload must include `evidence_quality`.

- `complete`: provider references and decision-useful evidence are available.
- `partial`: some provider references or source detail are available, but decision evidence is incomplete.
- `unavailable`: no decision-useful provider evidence is linked.
- `stale`: evidence exists but is outside the permitted freshness window.
- `provider_error`: provider call or detail fetch failed before evidence could be completed.

For all states except `complete`, RegMind must include `missing_reason` and, where possible, `next_action`. A partial/unavailable/stale/provider-error result must never be presented as clean or equivalent to a fresh complete result.

## Audit Requirements

CA/Mesh audit events must be durable application-level events and include safe provider references when available. Events should cover screening request, result receipt, provider failure, webhook receipt/deduplication where applicable, evidence incompleteness, hit review, false-positive clearance, true-match confirmation, escalation, follow-up request, rescreening, stale detection, and approval block/allow decisions tied to CA state.

## Logging And UI Restrictions

- Do not log Mesh credentials, tokens, cookies, webhook signatures, or full unredacted provider responses.
- UI technical panels may expose provider references and evidence quality, but not provider secrets.
- CloudWatch logs are supporting operational telemetry only; they are not the application audit source of truth.
