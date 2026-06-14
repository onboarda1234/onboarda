# PR-CA2 Diagnosis

## Scope

PR-CA2 re-diagnosed CA-003, CA-004, CA-011, CA-UX-004, and CA-UX-009 from current `origin/main`.

## Base

- `origin/main` SHA at diagnosis: `5d664a51fb0d6161095aff88f17a657b5e23cacd`
- Required PR-CA1 merge SHA present: yes, same SHA
- Branch: `codex/pr-ca2-ca-evidence-completeness-audit-chain`

## Findings

### Provider references

Current CA normalized reports preserved some profile and risk identifiers, but Mesh provider references were not consistently promoted as durable first-class evidence across provider, subject, hit, queue, review, and audit payloads.

Specific diagnosis:

- Mesh case IDs were not reliably promoted from resnapshot context into normalized provider references.
- Mesh alert IDs and risk IDs could be conflated in the normalizer because risk listing responses were modeled as alert responses without retaining the outer alert ID.
- Queue and detail evidence paths accepted explicit provider reference fields, but not all nested `provider_references` shapes.
- Application audit detail for screening review did not include a durable CA/Mesh event type, provider references, evidence quality, or before/after CA state.

### Evidence completeness

Queue evidence already had `evidence_status` and some reason labels, but PR-CA2 required a canonical `evidence_quality` contract and mandatory `missing_reason` or `next_action` for non-complete evidence.

Specific diagnosis:

- Partial or unavailable evidence had inconsistent API shape across queue/detail/review paths.
- Missing provider identifiers could be visible in diagnostics but were not promoted as a canonical evidence quality field.
- Subject review context did not consistently inherit evidence completeness from the queue evidence model.

### Audit chain

CA screening lifecycle events were not durable enough at application level.

Specific diagnosis:

- `/api/screening/run` wrote a generic `Screening` audit row, but not CA-specific requested/result/failure/evidence-incomplete events.
- Screening review audit rows were generic and did not preserve CA/Mesh provider references, evidence quality, or before/after state linked to the subject/hit.
- The application audit endpoint and UI activity filter had no CA/Mesh-specific timeline filter.

### Raw/redacted provider evidence policy

No focused policy existed to define what CA/Mesh evidence is preserved, summarized, redacted, or excluded from logs/API/UI.

### UI traceability

Evidence cards already displayed some provider technical references, but application audit activity did not provide a filtered CA/Mesh screening timeline. This PR keeps UI scope narrow and does not rebuild adverse-media parity UI.

## Out Of Scope Confirmed

- Full Mesh dashboard parity count reconciliation: PR-CA4.
- Deep adverse-media article-card UI rebuild: PR-CA4.
- Webhook retry/reconciliation job: PR-CA3.
- Country-risk governance: PR-CR.
- Agent lifecycle evidence: PR-DOC.
