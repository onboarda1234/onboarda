# Root Cause

The staging 503 was not caused by CA Sandbox being unreachable or by OAuth failure.

CA Sandbox authentication succeeded, but the strict create-and-screen payload included rich fields rejected by the CA Mesh Sandbox schema. Rejected company fields included registration number, jurisdiction, incorporation date, entity type, website, addresses, and custom fields. Rejected person fields included address/contact enrichment.

The two-pass orchestrator runs strict and relaxed passes. The relaxed pass succeeded, but the strict pass returned provider 400, and submit correctly failed closed with a retryable 503 rather than false-clearing screening.

## Fix Direction

The fix keeps the screening gate intact and narrows the CA create-and-screen payload to fields verified against Sandbox:

- Company strict payload: `legal_name`, optional `industry`
- Person strict payload: `full_name` or `last_name`, `date_of_birth`, `nationality`, `country_of_birth`

Internal richer application data remains available for risk scoring, audit, document review, and future provider schema expansion.

