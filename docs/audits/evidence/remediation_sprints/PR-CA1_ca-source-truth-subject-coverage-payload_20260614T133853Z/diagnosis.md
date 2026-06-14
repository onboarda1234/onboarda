# PR-CA1 Diagnosis

Base `origin/main` SHA: `e1cbe10348ed04b855e38ab99a98ec9037638b98`

Branch: `codex/pr-ca1-ca-source-truth-subject-coverage-payload`

## CA-001 - Provider Source Of Truth

Result: reproduced on base `origin/main`.

- `screening_config.py` still described runtime screening source-of-truth as `legacy` for all dimensions, even when `SCREENING_PROVIDER=complyadvantage` and `ENABLE_SCREENING_ABSTRACTION=true`.
- `/api/screening/status` mixed AML screening, Sumsub IDV/KYC, OpenCorporates registry/enrichment, and abstraction/fallback state in a way that did not make the active AML provider explicit.
- Runtime CA readiness existed, but status wording still carried legacy/KYB framing and did not cleanly identify ComplyAdvantage Mesh as AML sanctions/PEP/adverse-media screening.

## CA-005 - Intermediary Subject Coverage

Result: reproduced on base `origin/main`.

- Application submission and manual screening paths loaded intermediaries, but the CA adapter/routing contract only accepted company, directors, and UBOs.
- Normalized reports had no `intermediary_screenings` collection.
- Screening queue and terminality/evidence logic did not treat intermediaries as required screening subjects.
- Missing intermediary screening was silent rather than recorded as a blocking evidence gap.

## CA-006 - Thin Company/Entity Payload

Result: reproduced on base `origin/main`.

- CA company payload construction mostly sent `legal_name`, with limited jurisdiction support.
- Available identifiers such as company registration number, registered address, incorporation date, entity type, business activity/sector, and application reference were not consistently included.
- The payload builder needed to omit unavailable fields safely rather than fabricating placeholders.

## CA-008 / CA-UX-012 - Provider Terminology And Fallbacks

Result: reproduced on base `origin/main`.

- Back-office labels used generic "ComplyAdvantage" or old KYB/media wording instead of "ComplyAdvantage Mesh" for the AML screening provider.
- Some UI provider fallbacks could display unknown provider evidence as ComplyAdvantage.
- The provider filter and API integration panel could imply provider responsibilities incorrectly.

## CA-012 - Stale Docs/Runbook

Result: reproduced on base `origin/main`.

- README/runbook/context docs still contained stale provider wording that could imply Sumsub remained the AML screening source.
- Operator docs did not clearly separate CA Mesh AML screening, Sumsub IDV/KYC, and OpenCorporates registry/enrichment roles.

## Runtime Evidence Generated Locally

- `runtime_json/provider_status_ca_active.json`
- `runtime_json/entity_payload_enriched.json`
- `runtime_json/intermediary_gap_report.json`
- `screenshots/backoffice-provider-label-http-smoke.png`
