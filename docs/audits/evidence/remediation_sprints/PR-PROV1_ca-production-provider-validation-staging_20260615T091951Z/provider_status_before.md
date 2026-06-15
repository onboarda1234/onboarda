# PR-PROV1 Provider Status Before Switch

## Status

Captured before any PR-PROV1 credential switch.

## `/api/version`

```json
{
  "git_sha": "6e44c13d79066fa4751cf2050e61bc009d7f9356",
  "image_tag": "6e44c13d79066fa4751cf2050e61bc009d7f9356",
  "environment": "staging",
  "service": "regmind-backend"
}
```

## `/api/screening/status`

Observed:

- ComplyAdvantage Mesh is active AML provider.
- Sumsub remains IDV/KYC provider.
- OpenCorporates registry/enrichment is separate and simulated.
- Screening abstraction is enabled.
- CA fallback/simulation is disabled.
- Provider labels are safe.

Raw evidence:

- `runtime_json/screening_status_before.json`

## CA Credential Boundary

Observed from redacted ECS/Secrets Manager snapshot:

- CA API host: `api.mesh.complyadvantage.com`
- CA auth host: `api.mesh.complyadvantage.com`
- CA realm configured: yes, redacted.
- CA username configured: yes, redacted.
- CA password configured: yes, redacted.
- CA screening config ID configured: yes, redacted.
- CA webhook secret configured: yes, redacted.
- Inferred URL mode: production-domain Mesh URL.

No credential values were exposed.

## Credential-Only Provider Probe

OAuth-only probe result: PASS.

No screening request was sent and no token was persisted or printed.
