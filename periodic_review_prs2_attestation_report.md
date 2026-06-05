# PRS-2 Submit Bug Patch + Redeploy + Revalidation Report

## 1. Final Verdict

**PASS WITH MINOR ISSUES**

The PRS-2 blocking defect is fixed on staging. Client draft save, reload persistence, valid submit, submitted read-only state, officer read-only attestation visibility, and submitted audit writes all passed on the patched deploy.

Residual non-blocking issues remain:

- a recurring browser-console `404` resource error still appears on both portal and back-office flows, but it did not produce page failures, failed requests, or API `500`s during validation;
- the back-office `Periodic Review Queue` nav item highlights correctly and the queue DOM contains the expected `PR-20` row, but the visible shell continued rendering the dashboard surface instead of the queue table. Officer review access still worked through the linked lifecycle path.

## 2. Previous Failure Summary

The prior staging validation failed because valid portal submission after a saved draft returned `500` for:

- `POST /api/portal/applications/7005921a11a54934/periodic-review/submit`

CloudWatch showed:

- `TypeError: Object of type datetime is not JSON serializable`
- source: `/app/periodic_review_attestation.py:298`

Observed impact on the failing build:

- draft save worked;
- draft reload worked;
- submit validation worked;
- final valid submit crashed before persistence completed;
- draft audit existed;
- submitted audit did not exist.

## 3. Root Cause

Saved-draft state was being reused in the submit path with timestamp fields coming back from Postgres as native `datetime` objects. The submit payload was then serialized with `json.dumps(...)`, which failed when `saved_at` or related temporal fields were still non-string values.

## 4. Patch Summary

Two commits were deployed during remediation:

- `29e99f981a009b1077af6b0e3591c7b589f821f8` — fix the submit-path datetime serialization crash
- `cd096318d04beb830e36724a620454e42694ea85` — normalize PRS-2 attestation timestamps to UTC ISO strings consistently

Patch scope:

- added `_serialize_temporal(...)` in `arie-backend/periodic_review_attestation.py`;
- serialized `saved_at`, `submitted_at`, and reused draft timestamps before JSON persistence and response shaping;
- normalized naive datetimes to UTC before ISO serialization;
- preserved audit detail fields instead of dropping them.

## 5. Regression Tests Added

Focused regression coverage was added in `arie-backend/tests/test_periodic_review_attestation.py` for:

- direct submit-path serialization of an existing draft whose `client_attestation_saved_at` is a real `datetime`;
- end-to-end draft-save -> submit flow with timestamp assertions on:
  - response payload
  - reloaded portal payload
  - persisted DB payload
  - submitted audit detail

Expanded assertions also verify:

- `saved_at` and `submitted_at` are strings;
- submitted state is read-only after success;
- officer-side review detail still exposes the read-only attestation summary and risk visibility;
- client portal still hides risk data.

## 6. Tests Run

Local verification passed after the final `cd09631` patch:

- `python3 -m py_compile arie-backend/server.py arie-backend/db.py arie-backend/periodic_review_projection_service.py arie-backend/periodic_review_attestation.py`
- `pytest -q arie-backend/tests/test_periodic_review_attestation.py`
- `pytest -q arie-backend/tests/test_portal_periodic_review_attestation_static.py`
- `pytest -q arie-backend/tests/test_periodic_review_handlers.py arie-backend/tests/test_periodic_review_phase1_handlers.py arie-backend/tests/test_periodic_review_phase1_canonical.py`
- `pytest -q arie-backend/tests/test_monitoring_routing.py arie-backend/tests/test_monitoring_enrollment.py`
- `pytest -q arie-backend/tests/test_backoffice_monitoring_navigation_static.py arie-backend/tests/test_application_lifecycle_tab_shell_static.py`
- `pytest -q arie-backend/tests/test_auth.py arie-backend/tests/test_r9_portal_ownership.py arie-backend/tests/test_r10_portal_ownership.py arie-backend/tests/test_cm_rbac_portal.py`
- `pytest -q arie-backend/tests/test_portal_remediation.py`

Staging/browser verification passed on the patched deploy:

- focused portal attestation flow report: `/tmp/prs2-portal-attestation-pr20-final/report.json`
- authenticated back-office smoke report: `/tmp/regmind-staging-browser-smoke-prs2-pr20-final/report.json`
- focused back-office periodic-review lifecycle report: `/tmp/prs2-backoffice-periodic-review-pr20-final/report.json`

## 7. Deploy + Runtime Source Of Truth

- branch: `codex/prs2-periodic-review-attestation`
- final deployed SHA: `cd096318d04beb830e36724a620454e42694ea85`
- deploy workflow run: GitHub Actions `deploy-staging.yml` run `27023296706`
- deploy note: the first attempt on this run failed in `ci / docker-validate` because Docker Hub timed out pulling `python:3.11-slim`; rerunning failed jobs completed successfully
- ECS cluster/service: `regmind-staging / regmind-backend`
- ECS task definition after final deploy: `arn:aws:ecs:af-south-1:782913119880:task-definition/regmind-staging:431`
- ECS rollout state: `COMPLETED`
- public health check: `GET https://staging.regmind.co/api/health` -> `200`

Authenticated `/api/version` on the final deploy returned:

- `git_sha`: `cd096318d04beb830e36724a620454e42694ea85`
- `git_sha_short`: `cd09631`
- `image_tag`: `cd096318d04beb830e36724a620454e42694ea85`
- `build_time`: `2026-06-05T15:34:52Z`
- `environment`: `staging`
- `service`: `regmind-backend`

Result: staging was definitively serving the intended fixed revision.

## 8. Portal Validation Results

Primary validation target:

- review: `PR-20`
- periodic review id: `20`
- application id: `43c48fce79054acd`
- application ref: `ARF-2026-900110`

Validated successfully in the final browser run:

- client login succeeded;
- dashboard displayed the periodic review task card for `PR-20`;
- dashboard did **not** expose `Risk Rating` or `Risk Score`;
- attestation modal loaded exactly 8 questions;
- save draft returned `200`;
- hard reload + re-open preserved saved answers and comments;
- valid submit returned `200`;
- no submit `500` occurred;
- submitted modal rendered the read-only banner and submitted timestamp;
- dashboard task card changed to `Submitted`;
- hard reload + re-open preserved the submitted read-only state.

Captured evidence:

- portal screenshots:
  - `/tmp/prs2-portal-attestation-pr20-final/00-dashboard.png`
  - `/tmp/prs2-portal-attestation-pr20-final/00a-modal-initial.png`
  - `/tmp/prs2-portal-attestation-pr20-final/01-draft-saved.png`
  - `/tmp/prs2-portal-attestation-pr20-final/02-draft-reloaded.png`
  - `/tmp/prs2-portal-attestation-pr20-final/03-submitted-readonly.png`
  - `/tmp/prs2-portal-attestation-pr20-final/04-submitted-reopened.png`

Canonical timestamps observed after success:

- `saved_at`: `2026-06-05T16:03:37.050708+00:00`
- `submitted_at`: `2026-06-05T16:03:44.159606+00:00`

Additional persistence proof:

- previously fixed review `PR-18` / `ARF-2026-900106` remained `submitted` on the final deploy.

## 9. Back-Office Validation Results

Generic authenticated smoke passed on `ARF-2026-900110`:

- Applications
- Application Detail
- Lifecycle tab
- KYC Documents
- Screening Review
- AI Compliance Supervisor
- Activity Log
- Case Management
- Monitoring Alerts
- Monitoring Agents
- Lifecycle Queue
- EDD
- Change Management

Focused officer validation also passed for the read-only attestation summary:

- officer login succeeded;
- the application lifecycle detail for `ARF-2026-900110` loaded;
- Lifecycle rendered the client attestation card;
- officer could see:
  - `Submitted`
  - `Risk level: LOW`
  - `Declared material-change keys: directors_changed, transaction_volume_changed`
  - `Declaration accepted: Yes`
  - both flagged client comments

Captured evidence:

- smoke screenshots under `/tmp/regmind-staging-browser-smoke-prs2-pr20-final/`
- focused lifecycle screenshots:
  - `/tmp/prs2-backoffice-periodic-review-pr20-final/01-periodic-review-queue.png`
  - `/tmp/prs2-backoffice-periodic-review-pr20-final/02-lifecycle-attestation.png`

Minor issue observed:

- the `Periodic Review Queue` nav state became selected and the queue DOM contained the expected `ARF-2026-900110` row, but the visible shell continued showing the dashboard surface instead of the queue table. This did not block lifecycle access through the linked application lifecycle path, but it is a separate UI defect worth follow-up.

## 10. Audit Validation Results

For `ARF-2026-900110` / periodic review `20`, both required events exist:

- `periodic_review_attestation_draft_saved`
- `periodic_review_attestation_submitted`

Latest submitted audit detail includes the required linkage fields:

- `periodic_review_id: 20`
- `application_id: 43c48fce79054acd`
- `application_ref: ARF-2026-900110`
- `client_id: 21eb50f952e54634`
- `actor_user_id: 21eb50f952e54634`
- `material_change_question_keys: ["directors_changed", "transaction_volume_changed"]`
- `declaration_accepted: true`
- `submitted_at: 2026-06-05T16:03:44.159606+00:00`
- `source_surface: portal_periodic_review_attestation`

Latest draft audit detail also includes the same linkage fields with:

- `declaration_accepted: false`
- `material_change_question_keys: ["directors_changed"]`
- `submitted_at: null`

Officer API detail for review `20` also confirms canonical final state:

- `client_attestation_status: submitted`
- `client_attestation_status_label: Submitted`
- `client_attestation_saved_at: 2026-06-05T16:03:37.050708+00:00`
- `client_attestation_submitted_at: 2026-06-05T16:03:44.159606+00:00`
- `risk_level: LOW`

## 11. Console / Network Findings

Portal:

- no failed requests;
- no API `500` responses;
- submit returned `200`;
- one non-blocking browser-console `404` resource entry was recorded.

Back office:

- no failed requests;
- no API `500` responses;
- no blocking console errors in the authenticated smoke;
- one non-blocking browser-console `404` resource entry was recorded.

No new runtime evidence suggests the original PRS-2 submit bug persists.

## 12. Release Assessment

PRS-2’s blocking release defect is resolved on staging:

- datetime values are serialized safely;
- valid submit no longer crashes;
- response timestamps are ISO-serialized;
- draft and submitted audit writes both exist;
- client read-only submitted state works;
- officer read-only summary works;
- officer risk visibility remains intact;
- client portal still hides risk visibility.

Remaining issues are outside the original blocking defect and did not prevent the end-to-end PRS-2 attestation flow from completing successfully on staging.

## 13. Recommendation

Release verdict: **PASS WITH MINOR ISSUES**

Recommended follow-up after signoff:

- triage the back-office `Periodic Review Queue` visible-rendering inconsistency;
- triage the recurring non-blocking `404` resource console noise;
- keep PRS-3 unblocked from the PRS-2 submit-fix standpoint.
