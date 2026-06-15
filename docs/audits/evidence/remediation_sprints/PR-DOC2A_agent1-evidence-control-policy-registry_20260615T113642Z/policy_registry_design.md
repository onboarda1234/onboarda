# Policy Registry Design

Registry version: `DOC-POLICY-REGISTRY-v1`

## Structure

Agent 1 settings now present a lifecycle-wide document policy registry with search, lifecycle, gate behavior, and status filters.

Lifecycle sections:

- Entity Documents
- Person / KYC Documents
- EDD Evidence
- Change Management Evidence
- Periodic Review Evidence
- Monitoring / SAR Evidence
- Regulatory / Resource Evidence
- Technical Checks

Each policy card exposes:

- Document family name
- Lifecycle stage
- Policy ID/version
- When required
- Gate behavior
- Manual acceptance posture
- Re-screening trigger
- Material checks
- Technical checks
- Active/foundation status

## Required Families Covered

The registry includes entity documents, person/KYC documents, EDD SOW/SOF evidence, bank statements/reference, change-management evidence, periodic-review evidence, monitoring/SAR evidence, and regulatory/resource evidence.

Specific change baselines are represented for director change, UBO change, ownership percentage change, DOB correction, nationality correction, and related re-screening/risk recalculation markers.

## Unknown Handling

Unknown or unclassified document types are represented by `DOC-UNKNOWN-UNCLASSIFIED-v1`. The policy states that unclassified documents are blocked from automated reliance, routed for officer classification, and excluded from memo/approval reliance until classified and verified or manually accepted with reason.

## Resource Evidence

Regulatory/resource documents are treated as library-only unless relied on in a case, memo, policy, or decision. If relied upon, source/date/version verification is required.

## Autonomy Boundary

The registry prepares for later safe autonomy by representing classification, policy mapping, blockers, next actions, and re-screening triggers. This PR does not make Agent 1 approve, reject, waive, or override compliance decisions.
