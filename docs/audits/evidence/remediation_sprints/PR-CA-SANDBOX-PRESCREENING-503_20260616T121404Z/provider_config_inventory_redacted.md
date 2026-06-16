# Provider Config Inventory Redacted

## Staging Runtime

- AWS region: `af-south-1`
- ECS cluster: `regmind-staging`
- ECS service: `regmind-backend`
- Runtime task definition observed during diagnosis: `regmind-staging:593`

## ComplyAdvantage

- Provider: `ComplyAdvantage Mesh`
- Runtime provider key: `complyadvantage`
- API base host: `api.mesh.complyadvantage.com`
- Auth host: `api.mesh.complyadvantage.com`
- Realm: `regmind`
- Screening config identifier: present
- Username: set, redacted
- Password: set, redacted
- Fallback/simulation: disabled

## Non-Secret Labels Supported By Status Endpoint

The status endpoint now reads these non-secret labels when present in the API task environment:

- `COMPLYADVANTAGE_WORKSPACE_MODE=sandbox`
- `COMPLYADVANTAGE_WORKSPACE_LABEL=ca-sandbox`
- `COMPLYADVANTAGE_SCREENING_CONFIG_LABEL=regmind-default-screening-v1`

The labels do not contain provider credentials. If the deployment workflow cannot be edited by the current GitHub token scope, these labels can be set on the staging ECS task definition during the post-merge staging deployment step.

## Other Providers

- Sumsub IDV: live/configured in staging status
- OpenCorporates registry/enrichment: simulated/not configured in staging status

No API keys, OAuth secrets, tokens, webhook secrets, usernames, or passwords are recorded in this evidence pack.
