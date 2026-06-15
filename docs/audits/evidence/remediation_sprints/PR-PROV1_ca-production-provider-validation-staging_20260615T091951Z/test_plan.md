# PR-PROV1 Test Plan - CA Production Provider Validation on Staging

## Status

`READY TO SWITCH / AWAITING APPROVAL`

No credential switch, screening execution, webhook replay, or browser runtime test has been performed in this workstream yet. Staging currently reports ComplyAdvantage Mesh as active and points at the Mesh production-domain host, but PR-PROV1 still requires explicit operator approval, approved test subjects, and cost controls before any controlled provider screening is run.

## Guardrails

- Environment: staging only.
- Data: synthetic, internal, or explicitly authorized test data only.
- Case cap: not approved yet. Recommended cap: 5 applications / 15 subject screenings maximum unless the operator approves a lower cap.
- Secrets: no CA credentials, OAuth tokens, webhook secrets, bearer tokens, cookies, API keys, or session values may be written to evidence.
- Production readiness: this workstream does not certify RegMind production readiness or PR-7 readiness.
- Cost exposure: credential-only OAuth probe has no screening cost. Screening cost must be approved before running provider tests.

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

## Approval Required Before Runtime Screening

The following are not yet available and must be supplied before Phase 3/4:

- Explicit operator approval to keep/use the current Mesh production-domain CA credential configuration on staging for controlled validation.
- Exact test subject list.
- Confirmation that every subject is synthetic, internal, or explicitly authorized.
- Approved test case cap and expected CA cost exposure.
- Confirmation whether to keep current staging CA credential mode after testing or roll back.
- Confirmation that CA Mesh dashboard/webhook subscription is configured for `https://staging.regmind.co/api/webhooks/complyadvantage`.

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
- CA screening API call: NOT RUN; requires approval.

### B. Entity No-Hit Path

Required controlled data:
- Synthetic/internal/authorized entity expected to produce no material match.

Checks:
- terminal clear/no-hit when provider returns clean result.
- queue/detail/memo/gate agree.
- no false blockers.

Status: NOT RUN - awaiting approved test data and screening approval.

### C. Entity Unresolved Hit Path

Required controlled data:
- Synthetic/internal/authorized entity expected to produce review-required result, or a provider-approved test fixture.

Checks:
- review required.
- approval blocked.
- provider refs preserved.
- audit events captured.

Status: NOT RUN - awaiting approved test data and screening approval.

### D. Director/UBO Screening

Required controlled data:
- Synthetic/internal/authorized director and UBO subjects.

Checks:
- subject-level rows exist.
- provider refs preserved.
- memo/gate impact agrees with canonical CA truth.

Status: NOT RUN - awaiting approved test data and screening approval.

### E. Intermediary Screening

Required controlled data:
- Synthetic/internal/authorized intermediary subject, or an application intentionally missing enough intermediary data to prove evidence-gap behavior.

Checks:
- intermediary included in CA screening scope or explicitly evidence-gapped.
- unresolved intermediary hit/gap blocks where applicable.

Status: NOT RUN - awaiting approved test data and screening approval.

### F. Adverse Media

Required controlled data:
- Authorized provider result or fixture with adverse media.

Checks:
- adverse media visible in queue/detail.
- article title/source/date/snippet/URL shown where provider returns it.
- memo reflects adverse media or is stale/requires regeneration.

Status: NOT RUN - awaiting approved test data and screening approval.

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

Status: NOT RUN - awaiting approved test data and screening approval.

### I. Provider Failure/Pending

Required setup:
- Safe failure simulation or approved fixture. Do not intentionally disrupt live provider credentials without operator approval.

Checks:
- provider unavailable/pending/non-terminal states fail closed.
- no false clear.
- officer-visible error/pending state.
- audit evidence.

Status: NOT RUN - awaiting approved fixture/failure simulation approval.

### J. Approval Gate

Required controlled applications:
- One clean terminal no-hit case.
- One unresolved/stale/partial/provider_error case.

Checks:
- unresolved/stale/partial/provider_error CA state blocks approval.
- clean terminal no-hit can pass CA screening gate, assuming all other gates are satisfied.

Status: NOT RUN - awaiting approved test data and screening approval.

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
