# PR-PRS-C1 API Smoke

## Staging API Smoke

- Target: deployed staging backend task via ECS Exec, HTTP base `http://127.0.0.1:8080`
- Public staging version endpoint: `https://staging.regmind.co/api/version` (authenticated)
- Merge SHA under test: `dd162525aa07c64660f70ca8336c3834ebdfb898`
- Public `/api/version.git_sha`: `dd162525aa07c64660f70ca8336c3834ebdfb898`
- Public `/api/version.image_tag`: `dd162525aa07c64660f70ca8336c3834ebdfb898`
- Synthetic data prefix: `PRPRSC1-STAGING-20260619045040`
- Synthetic rows are fixture-marked and isolated to PR-PRS-C1 smoke cases.
- Result JSON: `logs/api_smoke_staging_results.json`
- Raw ECS Exec log: `logs/api_smoke_staging_ecs_raw.log`
- Credential handling: SCO token generated inside the staging backend task; token omitted.

## Scenario Results

- Confirmed risk elevation propagates: pass. Application `prprsc1-staging-20260619045040-elevate` moved MEDIUM -> HIGH; audit `canonical_risk_recomputed` records previous/confirmed/model/final levels.
- No automatic downgrade: pass. Application `prprsc1-staging-20260619045040-downgrade-floor` stayed HIGH and audit has `downgrade_prevented=true`.
- Material-change rescore gate: pass. Missing risk decision returned `409` with `material_change_risk_decision_required`; documented rationale completed with `200`.
- Next-cycle cadence follows final risk: pass. Next cycle risk `HIGH`, frequency `12` months, due `2027-01-01`.
- No-change regression: pass. Risk before `{'final_risk_level': 'HIGH', 'risk_level': 'HIGH'}`, after `{'final_risk_level': 'HIGH', 'risk_level': 'HIGH'}`, canonical recompute audit count `0`.

## Version Payload

```json
{
  "build_time": "2026-06-19T03:59:02Z",
  "environment": "staging",
  "git_sha": "dd162525aa07c64660f70ca8336c3834ebdfb898",
  "git_sha_short": "dd16252",
  "image_tag": "dd162525aa07c64660f70ca8336c3834ebdfb898",
  "provider_status": {
    "aml_screening": {
      "abstraction_enabled": true,
      "auth_probe": "not_run",
      "checked_at": "2026-06-19T04:50:40Z",
      "configuration_label": "regmind-default-screening-v1",
      "fallback_enabled": false,
      "fallback_mode": "disabled",
      "health": "not_probed",
      "mode": "sandbox",
      "provider": "ComplyAdvantage Mesh",
      "status": "live",
      "workspace_label": "ca-sandbox"
    },
    "identity_verification": {
      "provider": "Sumsub IDV/KYC",
      "scope": "individual_kyc_identity_verification",
      "status": "live"
    },
    "registry_kyb": {
      "provider": "OpenCorporates registry/enrichment",
      "status": "simulated"
    }
  },
  "service": "regmind-backend"
}
```
