# PR-PROV1 Issues Found

## Status

No product defect confirmed because controlled runtime provider screening was
not run.

## Readiness Gaps

### PROV1-GAP-001 - Explicit switch/test approval missing

- Status: resolved by operator input on 2026-06-15.
- Evidence: approved provider mode, subject list, case cap, billing cap, and
  webhook subscription confirmation were provided.

### PROV1-GAP-002 - Controlled test subject list missing

- Status: resolved by operator input on 2026-06-15.
- Evidence: approved subject list captured in `provider_status_before.md` and
  `runtime_test_cases.md`.

### PROV1-GAP-003 - Cost cap missing

- Status: resolved by operator input on 2026-06-15.
- Evidence: maximum `10` screening cases and `USD 50` expected exposure.

### PROV1-GAP-004 - Webhook dashboard subscription not independently confirmed

- Status: partially resolved by operator input on 2026-06-15.
- Evidence: operator confirmed the CA Mesh webhook subscription to staging.
- Remaining gap: no signed provider webhook was delivered because runtime
  screening was not started.

### PROV1-GAP-005 - Dashboard/account mode not independently confirmed

- Severity: Blocking for runtime validation.
- Evidence: API credential URLs are production-domain and OAuth succeeds, but a
  prior CA Mesh dashboard screenshot reportedly showed `Sandbox`.
- Required action: Provide redacted dashboard/account-mode evidence showing
  Production, or document why the prior Sandbox dashboard is not tied to the
  active staging API credentials.
- Safety action taken: no screening requests were sent.

## Corrective PRs

None recommended yet. If runtime validation later reveals misleading provider results, stale state, webhook loss, evidence loss, or gate bypass, create a corrective PR prompt before proceeding to PR-7.
