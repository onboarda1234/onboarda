# PR Closure Report

## PR name

`PR-CA1 - CA Source of Truth, Subject Coverage and Payload Hardening`

## Linked remediation IDs

- `CA-001`
- `CA-005`
- `CA-006`
- `CA-008`
- `CA-UX-012`
- `CA-012`

## Original issue summary

RegMind had live ComplyAdvantage capability, but provider source-of-truth, subject coverage, entity payload quality, UI terminology, and operator docs were not production-defensible.

## Re-diagnosis result

- Current `origin/main` SHA: `e1cbe10348ed04b855e38ab99a98ec9037638b98`
- Branch name: `codex/pr-ca1-ca-source-truth-subject-coverage-payload`
- Branch commit SHA: pending commit.
- Does the issue still exist on current `origin/main`? Yes. Reproduced before branch changes.
- Evidence: `diagnosis.md`, `runtime_json/`, and local tests listed in `test_results.md`.

## Root cause

The active AML provider integration had advanced faster than its governance and display surfaces. CA Mesh was callable, but old Sumsub/legacy assumptions remained in source-of-truth rules, screening subject orchestration, normalized models, approval evidence checks, UI fallbacks, and docs.

## Files changed

- `CLAUDE.md`
- `README.md`
- `arie-backoffice.html`
- `arie-backend/screening_adapter_sumsub.py`
- `arie-backend/screening_complyadvantage/adapter.py`
- `arie-backend/screening_complyadvantage/normalizer.py`
- `arie-backend/screening_complyadvantage/payloads.py`
- `arie-backend/screening_config.py`
- `arie-backend/screening_models.py`
- `arie-backend/screening_provider.py`
- `arie-backend/screening_routing.py`
- `arie-backend/screening_state.py`
- `arie-backend/security_hardening.py`
- `arie-backend/server.py`
- `arie-backend/tests/test_api.py`
- `arie-backend/tests/test_backoffice_ca_truthflow_static.py`
- `arie-backend/tests/test_backoffice_review_audit.py`
- `arie-backend/tests/test_complyadvantage_payloads.py`
- `arie-backend/tests/test_monitoring_alerts_sprint1_static.py`
- `arie-backend/tests/test_phase6_complyadvantage_readiness.py`
- `arie-backend/tests/test_provider_label_policy.py`
- `arie-backend/tests/test_screening_adapter_complyadvantage.py`
- `arie-backend/tests/test_screening_config.py`
- `arie-backend/tests/test_screening_mode.py`
- `arie-backend/tests/test_screening_provider.py`
- `arie-backend/tests/test_screening_routing.py`
- `docs/DEPLOYMENT_RUNBOOK.md`

## Behaviour before fix

- CA-active configuration could still report legacy source-of-truth.
- Status output did not clearly separate CA Mesh AML, Sumsub IDV/KYC, OpenCorporates registry/enrichment, abstraction, and fallback state.
- Intermediaries were not passed into CA screening.
- Missing intermediary data created silent coverage gaps.
- Company/entity CA payloads omitted available identifiers.
- UI labels and fallbacks could imply CA when provider evidence was unknown.
- Docs retained stale Sumsub/legacy AML wording.

## Behaviour after fix

- CA Mesh becomes the screening source-of-truth for screening report, approval gates, and back-office display only when `SCREENING_PROVIDER=complyadvantage` and `ENABLE_SCREENING_ABSTRACTION=true`.
- `/api/screening/status` distinguishes AML provider, IDV/KYC provider, registry/KYB provider, abstraction requirement, and fallback/simulation state.
- Intermediaries are included in CA screening scope.
- Missing intermediary name is recorded as a failed evidence gap and blocks readiness.
- Company/entity payload includes available identifiers and omits unavailable values safely.
- Back-office labels use `ComplyAdvantage Mesh` and missing provider evidence falls back to unknown rather than CA.
- Targeted docs explain CA Mesh, Sumsub, and OpenCorporates roles.

## Tests added/updated

- `arie-backend/tests/test_screening_config.py`
- `arie-backend/tests/test_complyadvantage_payloads.py`
- `arie-backend/tests/test_screening_adapter_complyadvantage.py`
- `arie-backend/tests/test_screening_routing.py`
- `arie-backend/tests/test_screening_mode.py`
- `arie-backend/tests/test_screening_provider.py`
- `arie-backend/tests/test_provider_label_policy.py`
- `arie-backend/tests/test_backoffice_ca_truthflow_static.py`
- `arie-backend/tests/test_backoffice_review_audit.py`
- `arie-backend/tests/test_monitoring_alerts_sprint1_static.py`
- `arie-backend/tests/test_phase6_complyadvantage_readiness.py`
- `arie-backend/tests/test_api.py`

## Targeted test results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_screening_config.py arie-backend/tests/test_complyadvantage_payloads.py arie-backend/tests/test_screening_adapter_complyadvantage.py arie-backend/tests/test_screening_routing.py arie-backend/tests/test_screening_mode.py arie-backend/tests/test_screening_provider.py arie-backend/tests/test_provider_label_policy.py arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_backoffice_review_audit.py::TestPhaseSixComplyAdvantageStatusUI::test_api_status_panel_lists_complyadvantage_with_correct_responsibility arie-backend/tests/test_monitoring_alerts_sprint1_static.py::test_monitoring_alert_detail_renders_compact_provider_evidence_without_fake_links arie-backend/tests/test_phase6_complyadvantage_readiness.py::test_complyadvantage_status_is_not_live_when_unconfigured arie-backend/tests/test_api.py::TestAuthenticatedAccess::test_screening_status_does_not_expose_unused_provider -q
```

Result:

```text
138 passed in 1.97s
```

## Full suite results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
First run: 3 stale provider-wording assertions failed, 5323 passed, 25 skipped.
Updated stale assertions: 3 passed in 1.56s.
Subsequent full-suite reruns blocked by native WeasyPrint/CFFI segmentation fault in local macOS environment.
```

GitHub CI: pending after PR opens.

## Browser test results, if applicable

- Browser: Chromium via Playwright Python.
- URL: `http://127.0.0.1:8765/arie-backoffice.html`
- Role: unauthenticated static UI smoke.
- Steps: loaded back-office HTML, stubbed `/api/config/environment`, checked provider filter labels, provider formatter output, unknown fallback, API integration label, absence of legacy KYB label, and console/page errors.
- Result: passed.
- Screenshot path: `screenshots/backoffice-provider-label-http-smoke.png`

## Staging deploy evidence

- Merged main SHA: pending.
- Deployment mechanism: pending.
- ECS/task/image evidence, if applicable: pending.
- Deployed at: pending.

## /api/version evidence

Endpoint:

```text
pending
```

Result:

```json
{
  "git_sha": "pending",
  "image_tag": "pending"
}
```

Verdict:

- [ ] `git_sha` equals merged main SHA
- [ ] `image_tag` equals merged main SHA

## API smoke test evidence

- Endpoint(s): pending staging `/api/screening/status`, `/api/version`.
- Role/token type: pending.
- Expected: CA Mesh active AML provider, Sumsub IDV/KYC separate, truthful fallback state, intermediary gap behavior, unknown provider not CA.
- Actual: pending.
- Raw evidence path: `runtime_json/` contains local smoke fixtures only.

## Browser smoke test evidence, if applicable

- URL: pending staging URL.
- Role: pending permitted officer/admin.
- Expected: provider terminology clear and truthful; unknown provider not CA; ComplyAdvantage Mesh terminology consistent.
- Actual: pending.
- Screenshot path: pending staging screenshots.
- Console/network notes: pending.

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-CA1_ca-source-truth-subject-coverage-payload_20260614T133853Z/`

## Remaining risks

- Full CI has not yet passed.
- PR has not yet been merged to main.
- Merged main has not yet been deployed to staging.
- Staging `/api/version`, API smoke, and browser smoke are pending.
- CA/Mesh result parity, adverse-media UI depth, full audit-chain completeness, and historical contradictory-state cleanup remain out of scope for PR-CA1.

## Items not closed by this PR

- PR-CA2, PR-CA3, PR-CA4, PR-7, DOC, and CR remediation work.
- Full CA/Mesh parity remediation.
- Full adverse-media UI rebuild.
- Full audit-chain completeness remediation.
- Historical contradictory screening-state cleanup.

## Final closure verdict

Choose one:

- `PARTIALLY FIXED`

Rationale:

Code, tests, local smoke, and evidence pack are complete for the scoped PR-CA1 fix, but the issues must remain open/partially fixed until PR merge, GitHub CI, staging deploy, `/api/version`, staging API smoke, and staging browser smoke pass.
