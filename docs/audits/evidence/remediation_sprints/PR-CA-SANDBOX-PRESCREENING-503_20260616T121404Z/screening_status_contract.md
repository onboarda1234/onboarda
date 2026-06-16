# Screening Status Contract

`GET /api/screening/status` remains back-office authenticated.

## Added Safe Fields

The endpoint now exposes non-secret provider proof fields:

- `provider_truth.active_aml_screening_mode`
- `provider_truth.active_aml_workspace_label`
- `provider_truth.active_aml_screening_config_id`
- `provider_truth.active_aml_screening_config_label`
- `provider_truth.last_provider_health_result`
- `provider_truth.last_token_auth_probe_result`
- `provider_truth.last_error_category`
- `complyadvantage.mode`
- `complyadvantage.workspace_label`
- `complyadvantage.screening_configuration_identifier`
- `complyadvantage.screening_configuration_label`
- `complyadvantage.api_base_url_host`
- `complyadvantage.auth_url_host`
- `complyadvantage.last_provider_health_result`
- `complyadvantage.last_token_auth_probe_result`
- `complyadvantage.last_error_category`
- `complyadvantage.updated_at`

## Probe Behavior

- Default `GET /api/screening/status` does not call the provider.
- `GET /api/screening/status?probe=1` performs an auth-token probe only.
- Probe output is categorized as `ok`, `unavailable`, `skipped`, or `not_run`.
- Probe errors are reduced to non-secret category and sanitized summary.

## Explicit Exclusions

The status endpoint must not expose:

- API keys
- OAuth secrets
- Bearer tokens
- Webhook secrets
- Passwords
- Raw provider secret values

