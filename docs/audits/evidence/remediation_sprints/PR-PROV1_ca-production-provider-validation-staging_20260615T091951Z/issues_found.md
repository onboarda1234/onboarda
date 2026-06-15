# PR-PROV1 Issues Found

## Status

No product defect confirmed yet because controlled runtime provider screening was not run.

## Readiness Gaps

### PROV1-GAP-001 - Explicit switch/test approval missing

- Severity: Blocking for runtime validation.
- Evidence: User requested the workstream but did not explicitly approve switching or retaining production CA credentials for controlled screening.
- Required action: Operator approval before Phase 3/4.

### PROV1-GAP-002 - Controlled test subject list missing

- Severity: Blocking for runtime validation.
- Evidence: No exact synthetic/internal/authorized subjects were provided.
- Required action: Provide application refs or subject details that are lawful and approved for CA production-provider screening on staging.

### PROV1-GAP-003 - Cost cap missing

- Severity: Blocking for runtime validation.
- Evidence: No approved case cap or expected CA billing exposure was provided.
- Required action: Approve test cap and usage monitoring owner.

### PROV1-GAP-004 - Webhook dashboard subscription not independently confirmed

- Severity: Blocking for webhook smoke.
- Evidence: RegMind route and secret are configured, but CA Mesh dashboard subscription target was not verified in PR-PROV1.
- Required action: Confirm CA Mesh webhook target and event subscriptions, or provide dashboard evidence with secrets redacted.

## Corrective PRs

None recommended yet. If runtime validation later reveals misleading provider results, stale state, webhook loss, evidence loss, or gate bypass, create a corrective PR prompt before proceeding to PR-7.
