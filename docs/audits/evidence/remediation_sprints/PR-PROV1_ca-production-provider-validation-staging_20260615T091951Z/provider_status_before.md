# PR-PROV1 Provider Status Before Switch

## Status

Captured before any PR-PROV1 credential switch and updated after operator
approval for controlled runtime screening.

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

## Post-Approval Preflight

Timestamp: `2026-06-15T09:43:56Z`

Operator approval was provided for controlled staging validation using only:

- Entity: `Multigate Technologies Limited`
- Director: `Stephen Margolis`
- UBO: `Sir Michael Lawrence Davis`
- Intermediary: `Gemrock UK Plc`

Caps:

- Screening case cap: `10`
- Expected CA billing/cost cap: `USD 50`
- Webhook subscription to staging: confirmed by operator

Authenticated staging preflight:

- `/api/version`: PASS, `git_sha` and `image_tag` both
  `6e44c13d79066fa4751cf2050e61bc009d7f9356`.
- `/api/screening/status`: PASS, ComplyAdvantage Mesh active as AML provider,
  Sumsub IDV/KYC separated, fallback disabled.
- ECS backend/worker image and env provenance: PASS, both aligned to
  `6e44c13d79066fa4751cf2050e61bc009d7f9356`.
- CA runtime config presence: PASS by task-definition env/secret names for
  API base URL, auth URL, screening config ID, realm, username, password, and
  webhook secret.
- API credential mode inference: `production_domain`.
- Dashboard visual/account mode: NOT independently confirmed in this run.

Evidence:

- `runtime_json/post_approval_preflight_redacted.json`
- `runtime_json/post_approval_ecs_runtime_redacted.json`

## Safety Stop

No runtime screening request was sent after approval because the prior CA Mesh
dashboard screenshot reportedly showed `Sandbox`, and this run could not
independently confirm the active dashboard/account mode as `Production`.

The API credential boundary is production-domain, but the workstream requires
intentional confirmation of Sandbox vs Production before spending screening
calls. The approved subject list and caps are therefore recorded, but the
controlled runtime test matrix remains blocked pending dashboard/account-mode
evidence.

## Credential-Only Provider Probe

OAuth-only probe result: PASS.

No screening request was sent and no token was persisted or printed.
