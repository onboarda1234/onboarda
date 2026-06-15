# PR-PROV1 Closure Report - CA Production Provider Validation on Staging

## Workstream

`PR-PROV1 - CA Production Provider Validation on Staging`

## Branch

`codex/pr-prov1-ca-production-provider-validation-staging`

## Base SHA

`6e44c13d79066fa4751cf2050e61bc009d7f9356`

## Objective

Validate that RegMind staging can safely use ComplyAdvantage Mesh production-provider credentials under controlled test conditions without misleading officers, leaking secrets, using uncontrolled real data, or weakening approval gates.

## Current Result

`BLOCKED / NEEDS EVIDENCE`

Technical readiness evidence is collected and operator approval has now been
provided. Controlled provider validation is still not complete because the
active CA Mesh dashboard/account mode could not be independently confirmed as
Production after prior dashboard evidence reportedly showed Sandbox.

## What Was Done

- Fetched latest GitHub state.
- Checked out latest `origin/main`.
- Created branch `codex/pr-prov1-ca-production-provider-validation-staging`.
- Confirmed PR-CA1, PR-CA2, PR-CA3, PR-CA4, PR-CA4B, and PR-CA4C are present in `main`.
- Confirmed staging `/api/version` matches deployed main SHA.
- Captured redacted `/api/screening/status`.
- Captured redacted ECS task definition/service rollback handles.
- Captured redacted Secrets Manager version metadata.
- Confirmed CA webhook route exists and webhook secret is configured.
- Ran a CA OAuth-only credential probe.
- Created controlled provider-validation test plan and evidence pack.
- Recorded operator-approved subject list, screening case cap, billing cap, and
  webhook subscription confirmation.
- Ran an authenticated post-approval preflight:
  - `/api/version` matched `6e44c13d79066fa4751cf2050e61bc009d7f9356`.
  - `/api/screening/status` showed ComplyAdvantage Mesh active, Sumsub IDV/KYC
    only, and fallback disabled.
  - ECS backend and worker runtime aligned to deployed main SHA.
  - CA API/auth hosts were production-domain.
- Stopped before runtime screening because dashboard/account mode remains
  unconfirmed.

## What Was Not Done

- No staging CA credential switch was performed.
- No screening request was sent to CA under PR-PROV1.
- No synthetic/internal/authorized screening case was created.
- No webhook smoke was run.
- No browser smoke was run for PR-PROV1 provider-backed cases.
- No CA dashboard before/after screenshot was captured.
- No dashboard/account mode switch was performed.
- No production readiness or PR-7 readiness was claimed.

## Provider Mode Before Switch

- `/api/screening/status`: ComplyAdvantage Mesh active.
- CA fallback/simulation: disabled.
- Sumsub: IDV/KYC only.
- OpenCorporates: registry/enrichment, simulated.
- CA hosts: `api.mesh.complyadvantage.com`, values redacted.
- API credential mode classification: inferred `production_domain`.
- Dashboard/account visual mode: NOT independently confirmed.
- Operator-approved runtime subjects:
  - `Multigate Technologies Limited`
  - `Stephen Margolis`
  - `Sir Michael Lawrence Davis`
  - `Gemrock UK Plc`
- Case cap: `10`.
- Expected billing/cost cap: `USD 50`.

## Provider Mode After Switch

Not applicable. No switch was performed. Current staging configuration was kept
unchanged.

## API Smoke Result

READ-ONLY PASS; RUNTIME BLOCKED.

- `/api/version`: PASS.
- `/api/screening/status`: PASS.
- CA OAuth-only probe: PASS.
- Post-approval authenticated preflight: PASS.
- API credential mode inference: `production_domain`.
- Dashboard/account mode: NOT TESTABLE in this run.
- Controlled screening API paths: NOT RUN.

## Webhook Smoke Result

BLOCKED / NEEDS EVIDENCE.

Operator confirmed the webhook subscription to staging, and RegMind route/secret
configuration exists. No signed provider webhook was delivered because no
runtime screening request was sent.

## Browser Smoke Result

BLOCKED / NEEDS EVIDENCE.

No provider-backed controlled case exists for officer-facing browser validation.
No dashboard screenshot was captured.

## Approval Gate Result

NOT RUN for PR-PROV1 provider-backed cases.

## Memo Impact Result

NOT RUN for PR-PROV1 provider-backed cases.

## Audit Evidence Result

Readiness/config evidence only. No provider-backed screening audit chain was exercised.

## Cost / Usage

- OAuth token probe only.
- Post-approval authenticated RegMind status checks only.
- Screening requests: 0.
- Expected CA screening cost incurred by this workstream so far: none.
- Approved cap remains unused: maximum `10` screening cases / `USD 50`.

## Rollback / Keep Decision

No switch was performed, so no rollback was required. Current staging config was
kept unchanged. Rollback plan is documented in `rollback_plan.md`.

## Evidence

- `test_plan.md`
- `pre_switch_config.md`
- `rollback_plan.md`
- `provider_status_before.md`
- `provider_status_after.md`
- `api_smoke.md`
- `browser_smoke.md`
- `webhook_smoke.md`
- `audit_evidence.md`
- `memo_gate_evidence.md`
- `cost_and_usage.md`
- `runtime_test_cases.md`
- `rollback_or_keep_decision.md`
- `issues_found.md`
- `runtime_json/staging_version.json`
- `runtime_json/screening_status_before.json`
- `runtime_json/pre_switch_runtime_snapshot.json`
- `runtime_json/ca_oauth_probe.json`
- `runtime_json/post_approval_preflight_redacted.json`
- `runtime_json/post_approval_ecs_runtime_redacted.json`

## Final Status

`BLOCKED / NEEDS EVIDENCE`

Required next inputs:

1. Redacted CA Mesh dashboard/account evidence showing the active account is
   Production, or written operator confirmation explaining why the prior
   Sandbox dashboard screenshot does not apply to the active API credentials.
2. If a dashboard production switch is required, before/after screenshots and
   confirmation that staging still uses only the approved subject/cost caps.
3. After mode confirmation, run the controlled matrix using only the approved
   subjects.

## Scope Control

No PR-7, DOC, CR, post-approval locking, or unrelated remediation work was started. No unrelated remediation item was marked closed.
