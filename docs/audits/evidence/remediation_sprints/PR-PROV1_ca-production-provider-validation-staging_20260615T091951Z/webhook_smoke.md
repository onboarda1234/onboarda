# PR-PROV1 Webhook Smoke

## Status

NOT RUN.

## Confirmed

- RegMind route exists: `POST /api/webhooks/complyadvantage`.
- Staging CA webhook secret is configured in Secrets Manager.
- Current task definition references `COMPLYADVANTAGE_WEBHOOK_SECRET` through secret wiring.
- Existing PR-CA3/CA4C regression coverage includes Standard Webhooks signature verification, idempotency, and reconciliation paths.

## Not Confirmed

- CA Mesh dashboard subscription currently points to `https://staging.regmind.co/api/webhooks/complyadvantage`.
- A signed provider webhook was delivered during PR-PROV1.
- A duplicate delivery was received and deduped in staging during PR-PROV1.
- Webhook audit events were created for a controlled provider-backed case during PR-PROV1.

## Required After Approval

Use an approved controlled provider event or safe fixture to verify:

- signed webhook delivery.
- signature validation.
- duplicate idempotency.
- no duplicate hits/evidence.
- CA-specific webhook audit events.

No webhook secrets or signatures may be stored in evidence.
