# PR-PROV1 API Smoke

## Status

PARTIAL.

Configuration and credential-only smoke passed. Controlled runtime screening smoke was not run because approval/test data is pending.

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

## Not Run

The following require explicit approval and authorized test subjects:

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
