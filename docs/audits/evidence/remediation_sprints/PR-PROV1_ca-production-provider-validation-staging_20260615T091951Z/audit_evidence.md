# PR-PROV1 Audit Evidence

## Status

BLOCKED / NEEDS EVIDENCE for provider-backed screening events.

## Available Evidence

Configuration/readiness evidence:

- `runtime_json/staging_version.json`
- `runtime_json/screening_status_before.json`
- `runtime_json/pre_switch_runtime_snapshot.json`
- `runtime_json/ca_oauth_probe.json`
- `runtime_json/post_approval_preflight_redacted.json`
- `runtime_json/post_approval_ecs_runtime_redacted.json`

## Not Available Yet

Because no controlled screening case was run, PR-PROV1 does not yet have
application-level audit evidence for:

- CA screening requested.
- CA request sent.
- CA result received.
- CA provider failure.
- CA webhook received/deduped.
- CA hit created/updated.
- CA evidence partial/unavailable/stale.
- officer review/disposition.
- approval blocked/allowed due to CA state.

## Required After Dashboard/Account-Mode Confirmation

For each controlled test case, capture redacted API/DB evidence showing:

- application ref.
- subject type.
- subject hash/redacted name.
- provider references where available.
- event type.
- before/after state where applicable.
- actor.
- timestamp.
- reason/disposition.

CloudWatch snippets may be supplementary only; RegMind API/DB/audit evidence must be the primary compliance proof.
