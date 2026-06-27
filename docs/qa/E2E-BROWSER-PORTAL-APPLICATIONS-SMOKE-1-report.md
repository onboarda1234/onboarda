# E2E-BROWSER-PORTAL-APPLICATIONS-SMOKE-1 Report

## Executive Verdict

**PASS WITH MINOR ISSUES**

The pilot-facing browser smoke passed the critical controls: clean submit returned quickly with no 504, the application did not remain draft in backend state, pricing and KYC continued, background screening completed, missing documents/IDV/case-stage gates blocked approval, declared PEP generated EDD/enhanced requirements, adverse media/material screening concern generated enhanced controls, and Evidence Pack/Risk PDF exports worked.

Two non-blocking issues were found:

- **P1 pilot clarity:** the portal sidebar application badge still showed `Draft` while the main journey was correctly at Pricing/KYC.
- **P2 log hygiene:** a successful browser Evidence Pack export was followed by a `GET /export-pack` 405 logged as an unhandled exception.

Final co-founder verdict: **ready for controlled paid pilot**. Fix the stale portal side badge before a high-stakes pilot demo if possible.

## Source And Runtime

- Audited `origin/main` SHA: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- Deployed staging SHA: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- `/api/version.image_tag`: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- Backend ECS task definition: `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:688`
- Worker ECS task definition: `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-verification-worker:136`
- Backend/worker image tag: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- `/api/health`: HTTP 200
- `/api/liveness`: HTTP 200
- Provider mode: ComplyAdvantage Mesh `sandbox`, fallback disabled

## Test Users And Apps

- Client portal user: generated staging test user `browser-smoke-20260626153811@example.test`
- Back-office officer: staging QA officer from AWS Secrets Manager, redacted in artifacts
- Clean app: `ARF-2026-920610`, `RegMind Browser Smoke 20260626153811 Ltd`
- PEP fixture: `ARF-2026-900330`
- Adverse fixture: `ARF-2026-900342`
- HIGH/EDD gate fixture: `ARF-E2E-085403-PREAPPRO-0164`

Browser automation: Python Playwright Chromium, headless, screenshots plus redacted HAR/network evidence.

Scope note: the clean application was seeded via authenticated API to create a deterministic draft, then the browser session drove portal login, submit from the browser origin, pricing acceptance, KYC upload, back-office review, screening review, and Evidence Pack export.

## Scenario Results

| Scenario | Result | Evidence |
|---|---:|---|
| Source reset and staging version match | PASS | `source-reset.log`, `api-version.json`, ECS summaries |
| Clean LOW/Fast Lane submit | PASS | HTTP 200 in `178.6 ms`; backend submit log `78.16 ms` |
| No draft limbo | PASS | Backend status `pricing_review`, then `kyc_documents` |
| Pricing flow | PASS | Pricing page shown; existing wording unchanged |
| KYC document flow | PASS | KYC page shown; dummy document uploaded |
| Missing docs block KYC/approval | PASS | KYC submit HTTP 400 with document blockers; approval HTTP 400 |
| Background screening | PASS | Job `sjob_797f46f3f005432e9deaa92ebea01094` completed |
| Back-office Applications/Application Review | PASS | App found, opened, status/risk/lane matched backend |
| Screening Review | PASS | Clear subjects shown; raw JSON hidden by default |
| Declared PEP EDD requirements | PASS | HIGH/EDD, PEP requirements visible, direct approval blocked |
| Adverse media/enhanced requirements | PASS | Screening concern controls visible, direct approval/terminal replay blocked |
| Evidence Pack/Risk PDF | PASS | Browser export succeeded; clean/PEP/adverse risk PDFs extracted |
| Browser console/page/network | PASS WITH EXPECTED 400 | Only expected KYC blocker 400; no page errors/request failures |
| CloudWatch/log scan | PASS WITH MINOR LOG ISSUE | No submit 503/504, no CA submit polling timeout, no worker crash |

## Clean Submit Proof

- Browser submit response: HTTP `200`, `178.6 ms`
- Response status: `pricing_review`
- Risk/lane: `LOW / Fast Lane`
- Submitted timestamp: `2026-06-26 15:38:24.057389`
- Screening state at submit: `pending`
- Screening job: `sjob_797f46f3f005432e9deaa92ebea01094`
- Worker completion: `completed_at=2026-06-26T15:38:35Z`, `attempt_count=1`
- Final clean app state: `kyc_documents`, `LOW / Fast Lane`
- Screening truth after worker: `completed_clear`, approval gate ready for screening only
- Approval still blocked by case stage, IDV, and document evidence gates

## Portal Result

Portal showed the pricing step after submit, then moved to KYC documents after pricing acceptance. The pricing notice remained unchanged. A dummy Certificate of Incorporation uploaded successfully and showed `Verification in progress`.

Expected blocker worked: submitting KYC with incomplete documents returned HTTP `400`, `kyc_verification_blocked=true`, with missing document and verification-running blockers.

Defect: the left sidebar application badge remained `Draft` while the main workflow was in Pricing/KYC. Backend state was correct, and no invalid resubmit button was shown.

## Back Office Result

Applications list found `ARF-2026-920610`. Application Review opened with:

- status `KYC Documents Required`
- activation blocked with unresolved controls
- risk `LOW`
- correct application reference and company
- KYC & Documents tab loaded
- uploaded document row visible
- Screening Review showed three clear subjects and no raw JSON by default

## PEP / EDD Result

Fixture `ARF-2026-900330` is an authoritative client-declared PEP case:

- party PEP state: `client_declared_pep=true`, `declared_pep=true`, `pep_status=declared_yes`, `is_pep=true`
- status: `pre_approval_review`
- risk/lane: `HIGH / EDD`
- enhanced review active: `true`
- trigger labels: `HIGH / VERY_HIGH risk`, `PEP`
- visible requirements include PEP declaration details, source of wealth evidence, and bank reference letters
- direct approval probe returned HTTP `400`
- risk PDF floor reason includes `declared_pep_present`

## Adverse Media / Enhanced Requirements Result

Fixture `ARF-2026-900342` is a deterministic adverse/material concern case:

- party PEP state remains clean: `is_pep=false`, `pep_status=declared_no`
- status: `rejected` terminal fixture; current diagnostics remain visible
- risk/lane: `HIGH / EDD`
- adverse truth states include `material_concern` and `adverse_media_hit`
- approval effect: `submit_to_compliance_required`
- enhanced trigger labels include `Screening concern`
- visible internal controls include screening disposition and senior review if material
- terminal approval replay returned HTTP `409`
- risk PDF floor reason includes `material_concern`

## Approval Gate Smoke

- Clean app approval probe: HTTP `400`, blocked because app is still `kyc_documents`
- Incomplete KYC submit: HTTP `400`, document blockers returned
- PEP/EDD direct approval probe: HTTP `400`
- HIGH/EDD fixture approval probe: HTTP `400`
- Adverse terminal fixture replay: HTTP `409`

No approval bypass was observed.

## Evidence Pack / Risk PDF

Browser export succeeded for `ARF-2026-920610` and downloaded:

- `browser-clean-evidence-pack-ARF-2026-920610.zip`
- `browser-clean-03_risk_assessment.pdf`
- `browser-clean-03_risk_assessment.txt`

Corrected API exports with UI section keys succeeded for clean, PEP, and adverse fixtures:

- `clean-corrected-03_risk_assessment-ARF-2026-920610.txt`
- `pep-corrected-03_risk_assessment-ARF-2026-900330.txt`
- `adverse-corrected-03_risk_assessment-ARF-2026-900342.txt`

Risk PDFs include base numeric score, base level, floor/escalation applied, floor reason, final/floored score, final classification, and onboarding lane.

## Browser Findings

- Console events: `1`
- Page errors: `0`
- Request failures: `0`
- Browser HTTP 4xx/5xx: `1`
- Downloads: `1`

The only browser console/network error was the deliberate `POST /submit-kyc` HTTP `400` blocker for incomplete KYC documents. The HAR was redacted in place; repeat secret scan found no JWTs, bearer tokens, AWS keys, presigned signatures, or generated client passwords.

## CloudWatch Findings

Window scanned: `2026-06-26T15:38:11Z` onward, log group `/ecs/regmind-staging`, `5619` events.

- Real submit 504: `0`
- Real submit 503: `0`
- CA polling timeout inside submit: `0`
- Worker crash/failure: `0`
- DB errors: `0`
- Duplicate screening job errors: `0`
- Mock fallback: `0`
- Related clean app events: `43`

The broad numeric search matched timestamp/debug false positives for `503/504`. One real log hygiene issue was found: after a successful Evidence Pack browser export, a `GET /api/applications/.../export-pack` returned 405 and was logged as an unhandled exception.

## Defects

### P0 Blockers

None.

### P1 Pilot Issues

1. Portal sidebar stale `Draft` badge after valid submit.
   - Repro: create/submit clean app from portal, observe main page routes to Pricing/KYC while left sidebar still shows the application badge as `Draft`.
   - Evidence: `portal-02-application-submit-success.png`, `portal-04-kyc-documents.png`, `portal-05-document-uploaded.png`.
   - Impact: backend state and main journey are correct, but the stale badge can confuse pilot users.

### P2 Polish / Operational Hygiene

1. Evidence Pack endpoint logs unsupported GET as unhandled exception.
   - Repro: run browser export from Back Office; export succeeds, then logs show `GET /api/applications/<id>/export-pack` 405 logged as unhandled.
   - Evidence: `logs/cloudwatch-unhandled_exception_or_traceback.json`, `logs/cloudwatch-log-scan-classified.json`.
   - Impact: no user-facing failure; creates noisy error-level logs.

## Evidence Index

- Test plan: `E2E-BROWSER-PORTAL-APPLICATIONS-SMOKE-1-test-plan.md`
- Runtime: `api-health.*`, `api-liveness.*`, `api-version.json`, `ecs-*-summary.json`
- Compact summary: `compact-evidence-summary.json`
- Browser logs: `browser/console-events.json`, `browser/page-errors.json`, `browser/request-failures.json`, `browser/http-4xx-5xx.json`, `browser/network.har`
- Portal screenshots: `portal-01-login-or-dashboard.png` through `portal-06-kyc-submit-blocked.png`
- Back-office screenshots: `backoffice-01-applications-list.png` through `backoffice-09-adverse-screening-review.png`
- API state: `api/clean-*.json`, `api/pep-fixture-*.json`, `api/adverse-fixture-*.json`
- Approval gates: `api/approval-*.json`
- Evidence packs/PDFs: `browser-clean-evidence-pack-ARF-2026-920610.zip`, `*-03_risk_assessment-*.pdf`, `*-03_risk_assessment-*.txt`
- CloudWatch scan: `logs/cloudwatch-log-scan-summary.json`, `logs/cloudwatch-log-scan-classified.json`, `logs/cloudwatch-related-clean-app-events.json`
- Secret scan: `secret-artifact-scan-after-redaction.txt`

## Recommended PRs

1. Refresh/update portal sidebar application status after submit, pricing accept, and KYC state transitions so it no longer displays stale `Draft`.
2. Handle `GET /api/applications/:id/export-pack` as a clean 405/404 without logging it as an unhandled exception, or prevent the browser path that triggers it after successful download.
