# PR-CA1 API Smoke

## Local Runtime JSON Smoke

Generated files:

- `runtime_json/provider_status_ca_active.json`
- `runtime_json/entity_payload_enriched.json`
- `runtime_json/intermediary_gap_report.json`

Validated locally:

- CA source-of-truth dimensions return `complyadvantage` when `SCREENING_PROVIDER=complyadvantage` and `ENABLE_SCREENING_ABSTRACTION=true`.
- Runtime status reports `provider_display_name=ComplyAdvantage Mesh`, `active=true`, `implementation_status=active`, and `simulation_fallback_enabled=false`.
- Sumsub display name remains separate from the AML provider.
- Unknown raw provider keys are preserved and blank/missing provider evidence falls back to unknown in UI.
- Entity payload includes available jurisdiction, registration number, registered address, incorporation date, entity type, business activity, and application reference.
- Missing intermediary subject name produces an `intermediary_screenings` evidence gap with `api_status=failed`, `screening_state=failed`, `requires_review=true`, and `any_non_terminal_subject=true`.

## Staging API Smoke

Status: pending.

Required after merge/deploy:

- `/api/screening/status` shows ComplyAdvantage Mesh as active AML provider.
- Sumsub is separately identified as IDV/KYC.
- Fallback/simulation state is truthful.
- Intermediaries are included in CA screening scope or explicitly recorded as evidence gaps.
- Unknown provider/source does not render as CA.
- `/api/version` `git_sha` and `image_tag` equal merged main SHA.
