# PR-PROV1 API Smoke

## Status

BLOCKED / NEEDS EVIDENCE.

Configuration, authenticated status, ECS provenance, and credential-only smoke
passed. Controlled runtime screening smoke was not run because dashboard/account
mode could not be independently confirmed as Production after a prior screenshot
reportedly showed Sandbox.

## Completed

### `/api/version`

PASS.

- `git_sha`: `6e44c13d79066fa4751cf2050e61bc009d7f9356`
- `image_tag`: `6e44c13d79066fa4751cf2050e61bc009d7f9356`

### `/api/screening/status`

PASS.

- ComplyAdvantage Mesh active as AML provider.
- Sumsub IDV/KYC separated.
- OpenCorporates registry/enrichment separated.
- fallback/simulation disabled.

### CA OAuth Probe

PASS.

- Operation: credential-only token acquisition.
- Screening requests sent: none.
- Token omitted from logs/evidence.
- Evidence: `runtime_json/ca_oauth_probe.json`.

### Post-Approval Authenticated Preflight

PASS for read-only checks.

- Staging QA officer login succeeded; token redacted.
- `/api/version`: PASS.
- `/api/screening/status`: PASS.
- ECS backend/worker runtime alignment: PASS.
- API credential mode inference: `production_domain`.
- Dashboard/account mode: NOT independently confirmed.
- Screening requests sent after approval: `0`.

Evidence:

- `runtime_json/post_approval_preflight_redacted.json`
- `runtime_json/post_approval_ecs_runtime_redacted.json`

## Not Run

The following were intentionally not run because the production-vs-sandbox
dashboard/account-mode conflict remains unresolved:

- Entity no-hit screening.
- Entity unresolved-hit screening.
- Director/UBO screening.
- Intermediary screening/gap runtime path.
- Adverse media provider result.
- Rescreen/stale path.
- Provider failure/pending path.
- Approval gate runtime path using provider-backed screening cases.

## Evidence

- `runtime_json/staging_version.json`
- `runtime_json/screening_status_before.json`
- `runtime_json/pre_switch_runtime_snapshot.json`
- `runtime_json/ca_oauth_probe.json`
- `runtime_json/post_approval_preflight_redacted.json`
- `runtime_json/post_approval_ecs_runtime_redacted.json`
