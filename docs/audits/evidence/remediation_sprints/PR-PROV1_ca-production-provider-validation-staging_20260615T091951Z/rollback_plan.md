# PR-PROV1 Rollback Plan

## Current Rollback Handles

Staging runtime:

- ECS cluster: `regmind-staging`
- API service: `regmind-backend`
- API task definition at snapshot: `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:581`
- Worker service: `regmind-verification-worker`
- Secret: `regmind/staging`
- CA webhook secret: `regmind/staging/complyadvantage/webhook-secret`

Redacted version evidence:

- `runtime_json/pre_switch_runtime_snapshot.json`

## Rollback Before Any Switch

No credential switch was performed in this workstream. If no future switch is approved, rollback is not required.

## Rollback If A Future Credential Switch Is Approved

Before switching:

1. Record current ECS task definition ARN.
2. Record current Secrets Manager `AWSCURRENT` version hash/stage for `regmind/staging`.
3. Record current webhook secret `AWSCURRENT` version hash/stage.
4. Confirm `/api/version`.
5. Confirm `/api/screening/status`.

If rollback is needed:

1. Restore the previous `regmind/staging` secret version or manually restore the redacted prior CA config from the secure password manager/Secrets Manager version history.
2. Restore the previous CA webhook secret if it changed.
3. Redeploy/restart `regmind-backend` ECS service using the previous safe task definition or a fresh task definition that points to the restored secret values.
4. Redeploy/restart `regmind-verification-worker` if worker provider config changed.
5. Wait for ECS services to stabilize.
6. Confirm `/api/version`.
7. Confirm `/api/screening/status`.
8. Save redacted post-rollback JSON evidence.

## Do Not Roll Back By

- Editing local `.env` files.
- Pasting credentials into terminal history, PR bodies, or evidence files.
- Disabling approval gates or enabling simulation fallback to hide provider failure.
- Changing production RegMind services.

## Keep Decision

Not applicable yet. No approved switch or runtime test was performed.
