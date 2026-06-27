# E2E-PORTAL-APPLICATIONS-PILOT-READINESS-2 Report

## Executive Verdict

Verdict: **PASS WITH MINOR ISSUES**

No P0 blocker was found. The core pilot path passed: a clean client submitted quickly, did not remain draft, moved to `pricing_review`, accepted pricing, moved to KYC documents, uploaded required documents, submitted KYC after document blockers were cleared, appeared correctly in Back Office, completed background ComplyAdvantage sandbox screening, and remained blocked from final approval by existing IDV gates.

Minor issues are evidence limitations rather than confirmed product defects: browser automation was not exposed in this Codex thread, so screenshots, browser console logs, and visual portal/back-office checks were not captured; live CA slow/failure and two-officer false-positive clearance were not mutated in staging and were covered by automated regressions.

Final co-founder verdict: **ready for controlled paid pilot**, with a browser screenshot pass recommended before external demo materials are reused.

## Source And Deployment

- Audited `origin/main` SHA: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- Local source worktree: `/tmp/regmind-pr601-postmerge-repo`
- Source reset evidence: `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/source-control-evidence.md`
- Deployed staging SHA: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- Deployed image tag: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- `/api/health`: HTTP 200
- `/api/liveness`: HTTP 200
- `/api/version.git_sha`: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- `/api/version.image_tag`: `27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- Backend task definition: `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:688`
- Worker task definition: `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-verification-worker:136`
- Backend image: `782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- Worker image: `782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:27996f8b76976f18a8ca7d75e5a396fbbf55f7cb`
- CA provider status: sandbox workspace, `workspace_label=ca-sandbox`, fallback disabled

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api-health.*`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api-liveness.*`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api-version.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/ecs-services-summary.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/ecs-backend-task-summary.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/ecs-worker-task-summary.json`

## Users And Roles

- Client portal user: `e2e2-de24ab6912@example.test`, fresh staging client for this run
- Back-office role: staging QA officer/SCO from AWS staging secret
- Limited negative role: client token used for unauthorized approval attempt
- Compliance/HIGH gate coverage: controlled staging HIGH/EDD fixtures plus automated approval gate regressions

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/user-role-matrix.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/staging-qa-user-redacted.json`

## Applications Tested

- Primary clean application: `ARF-2026-920607`, id `070a7d4722014bdf`, company `RegMind Pilot E2E de24ab6912 Ltd`
- HIGH/EDD fixture: `ARF-E2E-085403-PREAPPRO-0164`, id `500df1d4260d4d1f`
- Provider-only PEP fixture: `ARF-E2E-085403-PEPFP-6F7A`, id `eb37869832044f3d`
- Status matrix samples: captured for `draft`, `pricing_review`, `pre_approval_review`, `kyc_documents`, `kyc_submitted`, `submitted_to_compliance`, `approved`, and `rejected`

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/application-refs-used.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/applications-status-matrix.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/high-edd-provider-pep-runtime-probes-summary.json`

## Scenario Results

| Scenario | Result | Evidence |
| --- | --- | --- |
| 1. Clean LOW / Fast Lane client journey | PASS | Submit returned HTTP 200 in 427.9 ms, status `pricing_review`, LOW/Fast Lane persisted, pricing accept moved to `kyc_documents`, documents uploaded, KYC submitted. |
| 2. Missing documents block approval | PASS | KYC submit with only one uploaded doc returned HTTP 400 with 11 missing and 1 pending document blocker. |
| 3. Uploaded/verified document path | PASS | 12 required dummy docs uploaded and SCO manual-accepted with audit reason; supporting doc rejected; info-request path exercised; KYC submit returned HTTP 200. |
| 4. Background screening completion | PASS | Submit response showed pending provider screening; DB `screening_jobs` row was created and then succeeded; screening truth recomputed to `completed_clear`. |
| 5. Provider-only PEP unresolved | PASS | Controlled fixture shows party `is_pep=false` and declared no PEP, while provider screening truth has `pep_detected`, HIGH/EDD routing, and screening gate blockers. |
| 6. Provider-only PEP false-positive cleared | PASS, automated coverage | Focused PR #599 regression tests passed. No live two-officer clearance was mutated because no safe fresh false-positive clearance fixture was available for this run. |
| 7. True HIGH/EDD or unresolved screening | PASS | Controlled HIGH/EDD fixture stayed HIGH/EDD; direct approval probe returned HTTP 400: cannot approve until compliance review is complete. |

## Portal Clean Submit Result

- Application: `ARF-2026-920607`
- Submit endpoint: `POST /api/applications/070a7d4722014bdf/submit`
- HTTP status: 200
- Submit response time: 427.9 ms client-observed
- Status after submit: `pricing_review`
- Submitted timestamp: `2026-06-26 14:48:05.273438`
- Risk: LOW
- Final risk: LOW
- Lane: Fast Lane
- Screening after submit: pending provider state with job `sjob_7828f3002caf4924b326424a7d50f393`
- Submit attempt id: `submit_070a7d4722014bdf_1362c16d6441405c`
- Back-office list search by ref returned exactly one application, no duplicate or ghost application.

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/client-submit-response.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/backoffice-detail-after-submit.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/portal-applications-after-submit.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/portal-status-lookup-after-submit.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/applications-list-search-ref-after-submit.json`

## Pricing And KYC Flow

- Pricing accept returned HTTP 200.
- Application moved from `pricing_review` to `kyc_documents`.
- Existing KYC document expectations were returned by the application detail API.
- 12 required documents were uploaded using safe dummy PDFs.
- KYC submit before all documents were accepted returned HTTP 400 with explicit document blockers.
- After SCO manual acceptance of required synthetic documents, KYC submit returned HTTP 200 and status moved to `kyc_submitted`.
- No pricing wording or pricing process code was changed by this E2E. Visual wording was not browser-verified in this thread; backend transition and static portal regressions passed.

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/client-accept-pricing-response.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/client-detail-after-pricing.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/required-document-expectations.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/document-upload-summary.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/kyc-submit-missing-docs-blocked.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/kyc-submit-after-manual-acceptance.json`

## Applications Page Result

Result: PASS by API/list evidence.

- Search by application ref returned HTTP 200 and exactly one result for `ARF-2026-920607`.
- Status filter `pricing_review` returned HTTP 200.
- Status matrix filters returned HTTP 200 for all targeted states:
  - `draft`: total 109
  - `pricing_review`: total 93
  - `pre_approval_review`: total 33
  - `kyc_documents`: total 90
  - `kyc_submitted`: total 48
  - `submitted_to_compliance`: total 11
  - `approved`: total 47
  - `rejected`: total 13
- Primary application appeared in list with matching backend status/risk after submit and after KYC submit.

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/applications-list-search-ref-after-submit.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/applications-list-filter-pricing-review.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/applications-list-after-kyc-submit.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/applications-status-matrix.json`

## Portal Status Matrix Result

Result: PASS by API projection and static regression coverage, with browser visual limitation.

Covered states:

- `draft`
- `pricing_review`
- `pre_approval_review`
- `kyc_documents`
- `kyc_submitted`
- `submitted_to_compliance`
- `approved`
- `rejected`

Primary app status progression:

- Draft created
- Submit moved to `pricing_review`
- Pricing acceptance moved to `kyc_documents`
- KYC submit moved to `kyc_submitted`

No API evidence of draft restoration, duplicate application creation, or stale status was found. Focused portal status/static tests passed. Browser screenshots and console/page-error capture were not available in this thread.

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/portal-applications-draft.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/portal-applications-after-submit.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/portal-applications-after-pricing.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/portal-applications-after-kyc-submit.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/tests-focused-python311.log`

## KYC And Documents Result

Result: PASS.

- Required document list returned 12 required expectations.
- Upload succeeded for all 12 required dummy PDFs.
- Officer upload path was exercised with one supporting document.
- View and download endpoints returned successful redacted evidence.
- Supporting document reject path returned HTTP 200.
- Required document request-info path returned HTTP 200.
- Required documents were evidence-classified as `test_only_synthetic`.
- SCO manual acceptance of required docs used an explicit reason and audit-required metadata.
- KYC submit stayed blocked until document evidence gate reached ready state.
- Audit trail contains upload, document verification, evidence classification, document review, KYC blocked, and KYC submitted actions.

The accepted dummy documents are staging-only synthetic evidence and are not a real-world approval proof. Final approval still remained blocked by IDV.

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/documents-after-upload.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/documents-after-review.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/document-view-inline-redacted.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/document-download-redacted.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/document-presigned-fetch-result.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/db/ecs-db-snapshot.json`

## Screening Review Result

Result: PASS for clean completion and provider-only PEP truth model. Browser Screening Review UI layout was not visually captured.

Clean application:

- Background screening job was created at submit.
- Worker locked the job immediately and completed it in the background.
- Final async status: `completed`
- DB job status: `succeeded`
- Screening truth: `completed_clear`
- Screening adverse truth: `allow_direct_approval` from screening gate only
- No screening reviews were required for the clean case.

Provider-only PEP fixture:

- Fixture: `ARF-E2E-085403-PEPFP-6F7A`
- Party PEP state remained clean:
  - Director `is_pep=false`, `pep_status=declared_no`, `client_declared_pep=false`
  - UBO `is_pep=false`, `pep_status=declared_no`, `client_declared_pep=false`
- Provider truth showed `completed_match`, `pep_detected`, and `submit_to_compliance_required=true`.
- Gate blockers included `screening_adverse_truth` and document/IDV blockers.
- This confirms provider PEP evidence did not mutate party-level confirmed PEP state.

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/background-screening-poll-states.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/backoffice-detail-final.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/db/ecs-db-snapshot.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/provider-pep-fixture-summary.json`

## Risk And Approval Gate Result

Result: PASS.

Clean app:

- Final risk: LOW
- Lane: Fast Lane
- Screening gate after background completion: clear
- Document evidence gate after manual acceptance: ready
- Final approval still blocked by IDV, proving no approval bypass after synthetic document acceptance.
- Corrected approval probe returned HTTP 400:
  - `Approval blocked: Identity verification gate failed...`
- Client token decision attempt returned HTTP 403:
  - `Insufficient permissions`

HIGH/EDD fixture:

- Fixture `ARF-E2E-085403-PREAPPRO-0164` remained HIGH/EDD.
- Direct approval probe returned HTTP 400:
  - `Application is still in pre-review state 'pre_approval_review'. Cannot approve until compliance review is complete.`
- Enhanced review summary remained approval blocked.

Automated regressions:

- Focused suite passed 158 tests.
- Coverage included async submit, slow/failure/retry behavior, screening/adverse truth, PR #595 provider PEP separation, PR #599 false-positive clearance/risk recomputation, sanctions/prohibited handling, approval gates, portal status static coverage, KYC document action stability, and risk PDF.

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/approval-gate-corrected-after-kyc-submit.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/client-unauthorized-decision-attempt.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/high-edd-direct-approval-blocked.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/tests-focused-python311.log`

## Roles And Access Result

Result: PASS for approval/access controls exercised in this run, with browser back-office navigation not visually captured.

- Client token final-decision attempt returned HTTP 403 `Insufficient permissions`.
- SCO/officer final approval on the clean application after KYC remained blocked by IDV with HTTP 400.
- SCO/officer direct approval on controlled HIGH/EDD fixture returned HTTP 400 before compliance completion.
- Audit evidence identified the actor and role for governance attempts and document decisions.
- Submitted immutable-field editing was covered by focused static/backend regression tests, not a browser edit attempt in this run.

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/client-unauthorized-decision-attempt.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/approval-gate-corrected-after-kyc-submit.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/high-edd-direct-approval-blocked.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/db/ecs-db-snapshot.json`

## Evidence Pack And Risk PDF Result

Result: PASS.

- Evidence Pack JSON endpoint returned HTTP 200.
- Evidence Pack ZIP export returned HTTP 200.
- ZIP size: 83,220 bytes.
- ZIP SHA256: `dd6ccaa3ee970099d704ac5fc814bc67ddf1ca0c46d3ecb9840f358a6a1049b0`
- ZIP contained 21 entries, including:
  - `00_manifest.pdf`
  - `01_case_summary.pdf`
  - `02_client_submission.pdf`
  - `03_risk_assessment.pdf`
  - `04_screening_summary.pdf`
  - `05_officer_corrections.pdf`
  - `06_compliance_memo.pdf`
  - `07_audit_trail.csv`
  - uploaded documents

`03_risk_assessment.pdf` text extraction shows:

- Base numeric score: 27.7
- Base risk level: LOW
- Floor/escalation applied: No
- Floor/escalation reason: Not available
- Final/floored score: 27.7
- Final risk classification: LOW
- Onboarding lane: Fast Lane

No false declared/confirmed PEP wording appeared in the clean risk PDF text.

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/evidence-pack-ARF-2026-920607.zip`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/evidence-pack-json.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/api/evidence-pack-zip-entries.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/03_risk_assessment.pdf`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/03_risk_assessment.txt`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/pdf-render/03_risk_assessment_page-1.png`

## Log Scan Result

Result: PASS.

CloudWatch log group scanned:

`/ecs/regmind-staging`

Window:

- Start: `2026-06-26T14:41:13Z`
- End: captured in `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/logs/cloudwatch-log-scan-summary.json`

Patterns with zero events:

- submit 504
- submit 503
- ComplyAdvantage workflow polling timeout
- synchronous submit handler marker searched for this audit
- screening worker failure
- worker exception
- mock fallback
- database error
- duplicate screening job
- traceback
- unhandled exception

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/logs/cloudwatch-log-scan-summary.json`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/logs/cloudwatch-*.json`

## Test Results

Focused backend/API/static suite:

Command run with bundled Python 3.12 runtime in `/tmp/regmind-pr601-postmerge-repo/arie-backend`:

`python -m pytest tests/test_async_submit_screening.py tests/test_submit_resilience.py tests/test_screening_adverse_truth.py tests/test_pep_provider_detection_separation.py tests/test_risk_recomputation.py tests/test_approval_gate.py tests/test_portal_pilot_boundary_static.py tests/test_kyc_docs_action_stability_static.py tests/test_evidence_pack_risk_pdf.py`

Result:

- 158 passed
- 9 deprecation warnings
- 15.20 seconds

Other checks:

- `py_compile`: passed
- `git diff --check`: passed
- Secret scan: no token-like values, bearer credentials, AWS access key ids, or presigned URLs found in evidence
- Local repo status after validation: clean

Note:

An initial run using macOS system Python 3.9 failed at import time because the codebase uses newer Python union syntax. The bundled Python 3.12 run is the valid runtime-aligned result.

Evidence:

- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/tests-focused-python311.log`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/tests-focused.log`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/py-compile-focused.log`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/git-diff-check.log`
- `/tmp/regmind-e2e-portal-applications-pilot-readiness-2/secret-scan-summary.md`

## Defect List

### P0 Blocker

None found.

Specifically not observed:

- No clean submit 504.
- No draft limbo after valid submit.
- No approval bypass.
- No wrong final risk on tested clean path.
- No provider PEP party contamination on controlled fixture.
- No missing-document bypass.
- No HIGH/EDD direct approval success.
- No pending/failed screening approval bypass was found in automated regression coverage.

### P1 Pilot Issue

None confirmed.

### P2 Polish / Evidence Gaps

1. Browser screenshots, console logs, page errors, and browser request-failure logs were not captured.
   - Impact: API and static coverage passed, but visual portal/back-office evidence is incomplete for demo polish.
   - Reproduction: In this Codex thread, browser automation tools were not exposed by tool discovery.
   - Recommended PR/process: Add or expose a repeatable Playwright/browser E2E harness for this exact pilot pack.

2. Live CA slow/failure simulation was not run in staging.
   - Impact: No live staging mutation risk, but runtime proof for provider failure uses automated tests rather than live test double evidence.
   - Reproduction: No safe staging toggle/test double was available in this run; production CA workspace was not used.
   - Recommended PR/process: Add a staging-only deterministic CA slow/failure fixture or feature flag.

3. Provider-only PEP false-positive clearance was not executed live with two officers.
   - Impact: PR #599 behavior is covered by automated regression tests; live fixture mutation was avoided.
   - Reproduction: Search for the named historical fixture returned no safe fresh fixture for this run; available provider-PEP fixture was inspected read-only.
   - Recommended PR/process: Add seeded staging fixtures for unresolved provider-only PEP and two-officer false-positive clearance.

## Recommended PRs

No P0/P1 product fix PR is recommended from this run.

Recommended P2 QA/process PRs:

1. Add or expose a repeatable browser E2E harness for this pilot pack, including screenshots, console logs, page errors, and request failures.
2. Add staging-only deterministic CA slow/failure fixtures so pending/retrying/failed screening can be runtime-proven without uncontrolled provider calls.
3. Add seeded provider-only PEP false-positive fixtures with a safe two-officer clearance path for repeatable PR #599 runtime evidence.

## Exact Reproduction Steps For Defects

No product defect was confirmed.

P2 evidence gap reproduction:

1. Browser evidence gap:
   - Start this Codex thread and search for browser automation tools.
   - Tool discovery does not expose a callable browser controller.
   - Result: no screenshots, console logs, page errors, or request failures can be captured from this thread.

2. CA slow/failure runtime evidence gap:
   - Inspect staging version/provider config.
   - Confirm CA is sandbox live provider with fallback disabled.
   - No staging-only slow/failure toggle is available.
   - Result: live slow/failure proof is not run; automated tests cover provider pending/failure behavior.

3. Provider-only PEP clearance fixture gap:
   - Search for the historical PR #599 fixture and inspect available provider-PEP fixtures.
   - No safe fresh two-officer clearance fixture is available for mutation in this run.
   - Result: unresolved provider-only PEP is inspected live; clearance behavior relies on automated PR #599 regressions.

## Residual Risks

- Browser visual UX cannot be fully certified from this run because no screenshots or console logs were available.
- Dummy document acceptance proves workflow mechanics, not real evidence quality.
- Runtime provider failure/retry behavior is proven by automated tests, not by a live staging failure toggle.
- False-positive clearance/risk recomputation is proven by automated tests, while live staging inspection confirmed unresolved provider-only PEP separation and fail-closed state.

## Exclusions

Explicitly excluded per scope:

- Full Periodic Review E2E
- Full Change Management E2E
- ComplyAdvantage production workspace
- Uncontrolled live provider screenings
- Code fixes

## Evidence Index

Primary folder:

`/tmp/regmind-e2e-portal-applications-pilot-readiness-2/`

Key artifacts:

- Test plan: `E2E-PORTAL-APPLICATIONS-PILOT-READINESS-2-test-plan.md`
- API runner summary: `e2e-api-summary.json`
- Application refs: `application-refs-used.json`
- Source evidence: `source-control-evidence.md`
- Health/liveness/version: `api-health.*`, `api-liveness.*`, `api-version.*`
- ECS evidence: `ecs-services-summary.json`, `ecs-backend-task-summary.json`, `ecs-worker-task-summary.json`
- Clean submit: `api/client-submit-response.json`
- Pricing/KYC: `api/client-accept-pricing-response.json`, `api/required-document-expectations.json`, `api/kyc-submit-*.json`
- Documents: `api/documents-after-upload.json`, `api/documents-after-review.json`, `api/document-*.json`
- Screening/background job: `api/background-screening-poll-states.json`, `db/ecs-db-snapshot.json`
- Provider PEP fixture: `api/provider-pep-fixture-summary.json`
- HIGH/EDD gate: `api/high-edd-direct-approval-blocked.json`
- Approval gates: `api/approval-gate-corrected-after-kyc-submit.json`, `api/client-unauthorized-decision-attempt.json`
- Evidence Pack: `evidence-pack-ARF-2026-920607.zip`, `03_risk_assessment.pdf`, `03_risk_assessment.txt`
- PDF render: `pdf-render/03_risk_assessment_page-1.png`
- Test logs: `tests-focused-python311.log`, `py-compile-focused.log`, `git-diff-check.log`
- CloudWatch logs: `logs/cloudwatch-log-scan-summary.json`, `logs/cloudwatch-*.json`
- Secret scan: `secret-scan-summary.md`

## Final Verdict

**PASS WITH MINOR ISSUES**

The pilot-critical controls held:

- Clean submit is fast and durable.
- Application does not remain draft.
- Pricing/KYC progression works.
- Background screening job is created and completed by the worker.
- Screening/adverse truth recomputes.
- Provider-only PEP does not contaminate declared/confirmed party PEP.
- Missing documents block KYC submission.
- Final approval remains blocked by existing gates.
- HIGH/EDD direct approval is blocked.
- Evidence Pack and risk PDF export work.
- No timeout, mock fallback, worker crash, DB error, or duplicate screening job signature appeared in logs.

The product is ready for controlled paid pilot. Capture browser screenshots/console logs in a browser-enabled run before using this as external demo evidence.
