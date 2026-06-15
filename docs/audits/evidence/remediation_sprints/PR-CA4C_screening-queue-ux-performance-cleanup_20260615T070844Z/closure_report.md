# PR Closure Report

## PR name

`PR-CA4C - Screening Queue UX and Performance Cleanup`

## Linked remediation IDs

- `CA-UX-013`
- `CA-UX cleanup - remove legacy/narrow screening wording`
- `CA-UX cleanup - simplify Screening Queue filters`
- `CA-UX cleanup - make queue search universal`
- `CA-UX cleanup - reduce repetitive/noisy queue display while preserving defensibility`

## Original issue summary

The Screening Queue needed to be faster and easier for officers to use. The default view was too noisy, exposed duplicated search concepts, showed legacy/narrow sanctions wording, and returned heavy evidence detail in list rows.

## Re-diagnosis result

- Current `origin/main` SHA: `e51dea202171c572261010ea241cb3df186b1288`
- Branch name: `codex/pr-ca4c-screening-queue-ux-performance-cleanup`
- Branch commit SHA: pending commit
- Does the issue still exist on current `origin/main`? yes
- Evidence:
  - `diagnosis.md`
  - `root_cause.md`
  - `runtime_json/local_queue_payload_perf.json`
  - `runtime_json/local_browser_smoke.json`

## Root cause

The queue list and detail paths shared the same full row shape, so default list responses carried heavy provider evidence. The UI also retained older filter and wording patterns from a sanctions-focused queue rather than the current ComplyAdvantage Mesh AML workflow.

## Files changed

- `arie-backend/server.py`
- `arie-backoffice.html`
- `arie-backend/tests/test_screening_queue.py`
- `arie-backend/tests/test_backoffice_ca_truthflow_static.py`
- `arie-backend/tests/test_provider_label_policy.py`
- `docs/audits/evidence/remediation_sprints/PR-CA4C_screening-queue-ux-performance-cleanup_20260615T070844Z/`

## Behaviour before fix

- Default queue list returned full provider evidence and full screening evidence items.
- Officers saw both queue search and Application reference filter.
- Default type filter exposed `Individual`.
- Entity queue context used `Company sanctions screening...` wording.
- Queue search did not clearly support ARF or Mesh references.

## Behaviour after fix

- Default queue list returns summary rows and omits heavy evidence.
- Full evidence is loaded on row/detail demand.
- Queue search is the single visible universal search for subject, company, ARF, and Mesh references.
- `Individual` is hidden by default and appears as `Other person` only when uncategorized rows exist.
- Entity queue context uses broad AML wording.

## Tests added/updated

- `test_screening_queue_summary_payload_omits_heavy_evidence_until_requested`
- `test_screening_queue_universal_search_matches_application_subject_company_and_mesh_refs`
- `test_screening_queue_entity_pending_uses_broad_aml_wording`
- `test_screening_queue_available_type_filters_label_uncategorized_people_as_other_person`
- `test_screening_queue_summary_payload_respects_limit_and_offset`
- `test_backoffice_screening_queue_filter_bar_is_universal_and_not_redundant`
- `test_backoffice_screening_queue_hides_individual_filter_until_backend_reports_other_people`
- `test_backoffice_screening_queue_lazy_loads_full_evidence_before_detail_view`
- Updated provider-label static test for simplified `Source` filter wording.

## Targeted test results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_provider_label_policy.py arie-backend/tests/test_screening_queue.py -q
```

Result:

```text
61 passed in 1.72s
```

Additional targeted/regression results are recorded in `test_results.md`.

## Full suite results

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest arie-backend/tests -q
```

Result:

```text
5382 passed, 25 skipped in 204.23s (0:03:24)
```

## Browser test results, if applicable

- Browser: Playwright Chromium
- URL: `http://127.0.0.1:8765/arie-backoffice.html#screening`
- Role: controlled authenticated back-office UI state
- Steps:
  - Open Screening Queue.
  - Verify simplified filter bar.
  - Verify universal placeholder.
  - Verify no Application Reference filter.
  - Verify no default `Individual` type option.
  - Verify broad Entity AML wording.
  - Verify no console messages.
- Result: passed
- Screenshot path: `screenshots/local_screening_queue_filter_bar_authenticated.png`

## Staging deploy evidence

- Merged main SHA: pending
- Deployment mechanism: pending
- ECS/task/image evidence, if applicable: pending
- Deployed at: pending

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

- Endpoint(s): pending staging smoke
- Role/token type: pending
- Expected:
  - Summary list payload.
  - Pagination.
  - Universal search across subject/company/ARF/provider refs.
  - Detail evidence remains available.
  - Legacy wording absent.
  - CA4B and CA1/2/3 regressions pass.
- Actual: pending
- Raw evidence path: pending

## Browser smoke test evidence, if applicable

- URL: pending
- Role: pending
- Expected: simplified and responsive Screening Queue with detail evidence still accessible.
- Actual: pending
- Screenshot path: pending
- Console/network notes: pending

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/PR-CA4C_screening-queue-ux-performance-cleanup_20260615T070844Z/`

## Remaining risks

- The endpoint still builds/enriches recent application rows in Python before filtering/pagination. PR-CA4C reduces default payload and render cost, but a future DB/indexed summary optimization may be needed if staging or production data volume shows backend compute bottlenecks.
- Staging validation is still required before PR-CA4C can be closed.

## Items not closed by this PR

- No unrelated remediation item is closed by this PR.
- PR-7, DOC, CR, post-approval locking, and unrelated remediation work are out of scope.

## Final closure verdict

Choose one:

- `OPEN`

Rationale:

Local implementation and tests are complete, but PR-CA4C cannot be closed until the PR is merged, deployed to staging, `/api/version` matches the merged SHA, and staging API/browser smoke passes.
