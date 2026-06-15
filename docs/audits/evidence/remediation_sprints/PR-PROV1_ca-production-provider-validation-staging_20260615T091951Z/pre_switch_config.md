# PR-PROV1 Pre-Switch Configuration Snapshot

## Verdict

`BLOCKED / NEEDS EVIDENCE`

Technical readiness is confirmed for read-only staging/API checks, and operator
approval for controlled runtime screening is now recorded. Runtime provider
screening remains blocked because dashboard/account mode was not independently
confirmed as Production after prior dashboard evidence reportedly showed
Sandbox. No credential switch was performed.

## Source Of Truth

- Base `origin/main`: `6e44c13d79066fa4751cf2050e61bc009d7f9356`
- Branch: `codex/pr-prov1-ca-production-provider-validation-staging`
- Staging `/api/version`:
  - `git_sha`: `6e44c13d79066fa4751cf2050e61bc009d7f9356`
  - `image_tag`: `6e44c13d79066fa4751cf2050e61bc009d7f9356`

## CA Foundational PRs

Confirmed in current `main`:

- PR-CA1: `5d664a51fb0d6161095aff88f17a657b5e23cacd`
- PR-CA2: `6b6ea16881ae7f93a0eeb4256bb4f205692be757`
- PR-CA2 follow-up: `787ce4a26abfbaceaa043011df4e3f961fa4f418`
- PR-CA3: `9b210f3884f9cd3bf0c28d82457e8f2b1dac69ca`
- PR-CA3 corrective: `523ac8f1d93b2614eb3aa8286c255ea1cd8580eb`
- PR-CA4: `af766c94f3540c02d11b22404070ccaa4923310d`
- PR-CA4B: `e51dea202171c572261010ea241cb3df186b1288`
- PR-CA4C: `6e44c13d79066fa4751cf2050e61bc009d7f9356`

## Current Provider Status

Runtime status from `/api/screening/status`:

- Active AML screening provider: ComplyAdvantage Mesh
- Requested screening provider: `complyadvantage`
- Screening abstraction enabled: `true`
- CA implementation status: `active`
- CA fallback mode: `disabled`
- CA simulation fallback enabled: `false`
- Sumsub role: IDV/KYC only
- OpenCorporates registry/KYB status: simulated

Raw redacted evidence:

- `runtime_json/staging_version.json`
- `runtime_json/screening_status_before.json`
- `runtime_json/pre_switch_runtime_snapshot.json`

## Staging CA Configuration

Redacted configuration evidence:

- `SCREENING_PROVIDER`: `complyadvantage` in ECS task definition
- `ENABLE_SCREENING_ABSTRACTION`: `true` in ECS task definition
- CA API host: `api.mesh.complyadvantage.com`
- CA auth host: `api.mesh.complyadvantage.com`
- CA realm: configured, value redacted
- CA username: configured, value redacted
- CA password: configured, value redacted
- CA screening configuration ID: configured, value redacted
- CA default workflow ID: configured, value redacted
- CA webhook secret: configured, value redacted

Credential mode:

- Inferred from configured hosts: `production_domain`.
- Operator approved current CA production credentials for controlled staging
  validation.
- Dashboard/account visual mode remains unconfirmed in this run.

## Credential-Only Probe

OAuth-only probe:

- Operation: CA OAuth credential-only probe.
- Screening request sent: no.
- Token persisted: no.
- Token printed: no.
- Result: PASS.
- Elapsed: `1916.73ms`.
- Raw evidence: `runtime_json/ca_oauth_probe.json`.

## Current Readiness Gaps

The following are not yet confirmed:

- Redacted dashboard/account evidence showing active CA Mesh mode is Production,
  or written operator confirmation explaining why the prior Sandbox screenshot
  does not apply to the active staging API credentials.
- Before/after screenshot if dashboard production mode must be selected/switched.
- Runtime screening, webhook, browser, memo/gate, and audit smoke after the mode
  conflict is resolved.
