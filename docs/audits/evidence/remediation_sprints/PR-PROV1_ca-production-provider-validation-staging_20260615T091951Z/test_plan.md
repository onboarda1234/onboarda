# PR-PROV1 Test Plan - CA Production Provider Validation on Staging

## Status

`BLOCKED / NEEDS EVIDENCE`

No credential switch, screening execution, webhook replay, or browser runtime
test has been performed in this workstream yet. Staging currently reports
ComplyAdvantage Mesh as active and points at the Mesh production-domain host.
Operator approval, approved subjects, case cap, billing cap, and webhook
subscription confirmation are now recorded. Runtime screening remains blocked
until dashboard/account mode is intentionally confirmed as Production because a
prior CA Mesh dashboard screenshot reportedly showed Sandbox.

## Guardrails

- Environment: staging only.
- Data: synthetic, internal, or explicitly authorized test data only.
- Case cap: maximum `10` screening cases total.
- Secrets: no CA credentials, OAuth tokens, webhook secrets, bearer tokens, cookies, API keys, or session values may be written to evidence.
- Production readiness: this workstream does not certify RegMind production readiness or PR-7 readiness.
- Cost exposure: maximum expected CA usage/cost exposure is `USD 50`.
- Approved subjects only: `Multigate Technologies Limited`, `Stephen
  Margolis`, `Sir Michael Lawrence Davis`, and `Gemrock UK Plc`.

## Prerequisites Confirmed

- `origin/main`: `6e44c13d79066fa4751cf2050e61bc009d7f9356`
- PR-CA1 through PR-CA4C are merged into current `main`.
- Staging `/api/version` matches current main SHA.
- `/api/screening/status` shows:
  - AML provider: ComplyAdvantage Mesh
  - IDV/KYC provider: Sumsub IDV/KYC
  - Registry/KYB provider: OpenCorporates registry/enrichment, currently simulated
  - fallback/simulation: disabled for CA
- CA OAuth credential-only probe: PASS. No screening request sent.
- Post-approval authenticated preflight: PASS. No screening request sent.

## Evidence Required Before Runtime Screening

The following must be supplied before Phase 3/4:

- Redacted CA Mesh dashboard/account evidence showing the active mode is
  Production, or written operator confirmation explaining why the prior Sandbox
  dashboard screenshot does not apply to the active staging API credentials.
- If a production-mode dashboard switch is needed, before/after screenshots.
- Confirmation that the approved subject list/case cap/cost cap still apply
  after any dashboard/account-mode switch.

## Controlled Test Matrix

### A. Provider Activation

Expected:
- CA Mesh production credentials authenticate.
- CA OAuth/token works.
- CA API returns expected response.
- RegMind status shows CA Mesh active.
- fallback/simulation disabled.

Current evidence:
- `/api/screening/status`: PASS for active provider/fallback state.
- OAuth-only probe: PASS.
- CA screening API call: NOT RUN; blocked pending dashboard/account-mode
  confirmation.

### B. Entity No-Hit Path

Approved controlled data:
- `Multigate Technologies Limited`, if expected to produce no material match.

Checks:
- terminal clear/no-hit when provider returns clean result.
- queue/detail/memo/gate agree.
- no false blockers.

Status: NOT RUN - blocked pending dashboard/account-mode confirmation.

### C. Entity Unresolved Hit Path

Approved controlled data:
- `Multigate Technologies Limited`, if expected to produce review-required
  result, or a provider-approved fixture using only approved data.

Checks:
- review required.
- approval blocked.
- provider refs preserved.
- audit events captured.

Status: NOT RUN - blocked pending dashboard/account-mode confirmation.

### D. Director/UBO Screening

Approved controlled data:
- Director: `Stephen Margolis`
- UBO: `Sir Michael Lawrence Davis`

Checks:
- subject-level rows exist.
- provider refs preserved.
- memo/gate impact agrees with canonical CA truth.

Status: NOT RUN - blocked pending dashboard/account-mode confirmation.

### E. Intermediary Screening

Approved controlled data:
- Intermediary: `Gemrock UK Plc`

Checks:
- intermediary included in CA screening scope or explicitly evidence-gapped.
- unresolved intermediary hit/gap blocks where applicable.

Status: NOT RUN - blocked pending dashboard/account-mode confirmation.

### F. Adverse Media

Approved controlled data:
- Provider result from approved subjects only, or provider-approved fixture using
  only approved data.

Checks:
- adverse media visible in queue/detail.
- article title/source/date/snippet/URL shown where provider returns it.
- memo reflects adverse media or is stale/requires regeneration.

Status: NOT RUN - blocked pending dashboard/account-mode confirmation.

### G. Webhook

Required setup:
- Confirm CA Mesh webhook subscription targets `https://staging.regmind.co/api/webhooks/complyadvantage`.
- Confirm Standard Webhooks signature secret matches staging.

Checks:
- signed webhook delivered.
- signature validation succeeds.
- duplicate webhook is idempotent.
- audit events recorded.
- no duplicate hits/evidence.

Status: NOT RUN - webhook subscription delivery not confirmed and no provider event authorized.

### H. Rescreen/Stale

Required controlled data:
- Safe rescreen path for a synthetic/internal/authorized application.

Checks:
- old result stale/superseded.
- new result updates freshness.
- memo/gate staleness behavior correct.

Status: NOT RUN - blocked pending dashboard/account-mode confirmation.

### I. Provider Failure/Pending

Required setup:
- Safe failure simulation or approved fixture. Do not intentionally disrupt live provider credentials without operator approval.

Checks:
- provider unavailable/pending/non-terminal states fail closed.
- no false clear.
- officer-visible error/pending state.
- audit evidence.

Status: NOT RUN - blocked pending dashboard/account-mode confirmation and safe
fixture/failure simulation approval.

### J. Approval Gate

Required controlled applications:
- One clean terminal no-hit case.
- One unresolved/stale/partial/provider_error case.

Checks:
- unresolved/stale/partial/provider_error CA state blocks approval.
- clean terminal no-hit can pass CA screening gate, assuming all other gates are satisfied.

Status: NOT RUN - blocked pending dashboard/account-mode confirmation.

### K. Browser Smoke

Required:
- Staging browser session using an approved officer/admin.

Checks:
- provider status visible.
- Screening Queue understandable.
- Application Screening Review opens.
- adverse media visible where present.
- Mesh refs/details available through progressive disclosure.
- blocker and next action visible.
- no console/network errors.

Status: NOT RUN - browser smoke after controlled screening is awaiting approved runtime cases.
