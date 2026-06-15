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

`READY TO SWITCH / AWAITING APPROVAL`

Technical readiness evidence is collected, but controlled provider validation is not complete because explicit operator approval, approved test subjects, cost cap, and webhook dashboard confirmation are missing.

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

## What Was Not Done

- No staging CA credential switch was performed.
- No screening request was sent to CA under PR-PROV1.
- No synthetic/internal/authorized screening case was created.
- No webhook smoke was run.
- No browser smoke was run for PR-PROV1 provider-backed cases.
- No production readiness or PR-7 readiness was claimed.

## Provider Mode Before Switch

- `/api/screening/status`: ComplyAdvantage Mesh active.
- CA fallback/simulation: disabled.
- Sumsub: IDV/KYC only.
- OpenCorporates: registry/enrichment, simulated.
- CA hosts: Mesh production-domain hostnames, values redacted.
- Credential mode classification: inferred `production_domain_or_unsuffixed_provider_url`; operator confirmation still required.

## Provider Mode After Switch

Not applicable. No switch was performed.

## API Smoke Result

PARTIAL PASS.

- `/api/version`: PASS.
- `/api/screening/status`: PASS.
- CA OAuth-only probe: PASS.
- Controlled screening API paths: NOT RUN.

## Webhook Smoke Result

NOT RUN.

## Browser Smoke Result

NOT RUN.

## Approval Gate Result

NOT RUN for PR-PROV1 provider-backed cases.

## Memo Impact Result

NOT RUN for PR-PROV1 provider-backed cases.

## Audit Evidence Result

Readiness/config evidence only. No provider-backed screening audit chain was exercised.

## Cost / Usage

- OAuth token probe only.
- Screening requests: 0.
- Expected CA screening cost incurred by this workstream so far: none.
- Cost cap and CA usage monitoring owner still required before runtime tests.

## Rollback / Keep Decision

No switch was performed, so no rollback was required. A rollback plan is documented in `rollback_plan.md`.

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
- `cost_and_usage.md`
- `issues_found.md`
- `runtime_json/staging_version.json`
- `runtime_json/screening_status_before.json`
- `runtime_json/pre_switch_runtime_snapshot.json`
- `runtime_json/ca_oauth_probe.json`

## Final Status

`READY TO SWITCH / AWAITING APPROVAL`

Required next inputs:

1. Explicit operator approval to use current CA production-domain credentials for controlled staging screening, or explicit instruction to switch to another approved CA production credential set.
2. Exact synthetic/internal/authorized test subject list.
3. Approved test cap and cost exposure.
4. CA Mesh webhook dashboard subscription confirmation.
5. Rollback/keep decision criteria after testing.

## Scope Control

No PR-7, DOC, CR, post-approval locking, or unrelated remediation work was started. No unrelated remediation item was marked closed.
