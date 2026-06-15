# PR-CA4C Diagnosis - Screening Queue UX and Performance Cleanup

## Scope

Target remediation:

- CA-UX-013 - Screening Queue load performance and pagination optimization.
- CA-UX cleanup - remove legacy/narrow screening wording.
- CA-UX cleanup - simplify Screening Queue filters.
- CA-UX cleanup - make queue search universal.
- CA-UX cleanup - reduce repetitive/noisy queue display while preserving defensibility.

## Source of Truth

- Repository: `onboarda1234/onboarda`
- Branch: `codex/pr-ca4c-screening-queue-ux-performance-cleanup`
- Current `origin/main` SHA used for diagnosis: `e51dea202171c572261010ea241cb3df186b1288`
- Dependency confirmation: `origin/main` includes PR-CA4B merge SHA `e51dea202171c572261010ea241cb3df186b1288`.

## Re-Diagnosis Findings

### 1. Legacy / Narrow Wording

Finding:

- The Screening Queue entity row context still used narrow legacy wording such as `Company sanctions screening pending`, `Company sanctions screening unavailable`, and related simulated/not-configured labels.
- This wording is too narrow because ComplyAdvantage Mesh is the AML screening source of truth for sanctions, PEP/RCA, watchlist, and adverse media screening.

Evidence:

- Source: `arie-backend/server.py`, `_build_screening_queue_payload`.
- Local regression added: `test_screening_queue_entity_pending_uses_broad_aml_wording`.

Verdict:

- Reproduced on current `origin/main`.

### 2. Filter Bar Noise

Finding:

- The Screening Queue had both a general queue search and a separate Application reference filter.
- This duplicated search concepts and forced officers to understand which search box applied to ARF/company values.

Evidence:

- Source: `arie-backoffice.html`, Screening Queue filter bar.
- Local static regression added: `test_backoffice_screening_queue_filter_bar_is_universal_and_not_redundant`.

Verdict:

- Reproduced on current `origin/main`.

### 3. Type Filter Confusion

Finding:

- The default type filter exposed `Individual` next to `Director`, `UBO`, and `Intermediary`.
- In officer workflow terms this can read as a duplicate of the person roles rather than an uncategorized person bucket.

Evidence:

- Source: `arie-backoffice.html`, `screening-filter-type`.
- Local static/backend regressions added:
  - `test_backoffice_screening_queue_hides_individual_filter_until_backend_reports_other_people`
  - `test_screening_queue_available_type_filters_label_uncategorized_people_as_other_person`

Verdict:

- Reproduced on current `origin/main`.

### 4. Universal Search Gaps

Finding:

- Queue search did not clearly tell officers it could search ARF/application references or Mesh references.
- Backend search did not reliably index provider reference arrays from the evidence summary for summary-mode list rows.

Evidence:

- Source: `arie-backend/server.py`, `_screening_queue_search_blob`.
- Local regression added: `test_screening_queue_universal_search_matches_application_subject_company_and_mesh_refs`.

Verdict:

- Reproduced on current `origin/main`.

### 5. Heavy List Payload

Finding:

- `/api/screening/queue` returned full provider evidence and full `screening_evidence.items` in the default list payload.
- The queue list is a workbench and should be summary-first. Full Mesh/provider evidence should be loaded when opening the row/detail view.

Evidence:

- Source: `arie-backend/server.py`, `_build_screening_queue_payload`.
- Controlled local fixture: `runtime_json/local_queue_payload_perf.json`.
- Before-style full evidence mode: `422208` bytes for 50 returned rows.
- Summary mode after fix: `318596` bytes for 50 returned rows.
- Payload reduction: `24.54%`.

Verdict:

- Reproduced on current `origin/main`.

### 6. Backend Pagination / Search Architecture

Finding:

- The queue endpoint has API-level `limit` and `offset` pagination and returns pagination metadata.
- The current implementation still builds/enriches recent application rows in Python before filtering/paginating. PR-CA4C reduces response payload and UI render weight but does not fully move search/count computation into SQL.

Evidence:

- Source: `arie-backend/server.py`, `_build_screening_queue_payload`.
- Local regression added: `test_screening_queue_summary_payload_respects_limit_and_offset`.

Verdict:

- Partially optimized in this PR; deeper DB-side query optimization remains a future hardening item if staging data volume shows the Python enrichment scan is the bottleneck.

## Diagnosis Result

The PR-CA4C issues exist on current `origin/main`. The smallest safe fix is to:

- Replace sanctions-only entity wording with broad entity AML wording.
- Make queue search universal and remove redundant visible ARF filter.
- Hide the uncategorized person filter by default and label it `Other person` only when present.
- Return summary-mode queue rows by default and lazy-load full evidence on detail view.
- Preserve provider refs in searchable summary data without cluttering default rows.

