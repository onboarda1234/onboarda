# PR-CA4 Targeted Test Results

## Environment

- Python used for valid runs: `/opt/homebrew/bin/python3.11`
- System `python3` / Python 3.9 is not suitable for the current repo because `origin/main` imports Python 3.10+ union type syntax.

## Static and Compile Checks

```bash
git diff --check
```

Result: PASS.

```bash
python3 -m py_compile arie-backend/server.py arie-backend/memo_handler.py
```

Result: PASS when run with the Python 3.11 interpreter path.

## Focused PR-CA4 Regression Tests

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_phase3_memo_integrity.py::test_memo_consumes_canonical_company_adverse_media_evidence_without_rollup_flag \
  arie-backend/tests/test_screening_queue.py::test_screening_queue_links_ca_evidence_by_exact_identifiers \
  arie-backend/tests/test_screening_queue.py::test_screening_queue_rolls_up_current_duplicate_stale_and_historical_risks \
  arie-backend/tests/test_screening_queue.py::test_screening_queue_reports_structured_evidence_unavailable_honestly \
  arie-backend/tests/test_backoffice_ca_truthflow_static.py::test_backoffice_screening_evidence_drawer_normalizes_categories_and_sections \
  arie-backend/tests/test_backoffice_ca_truthflow_static.py::test_backoffice_screening_queue_renders_structured_evidence_readiness_panel \
  arie-backend/tests/test_backoffice_ca_truthflow_static.py::test_backoffice_screening_queue_source_links_are_conditional \
  arie-backend/tests/test_backoffice_ca_truthflow_static.py::test_backoffice_screening_disposition_modal_matches_api_contract \
  arie-backend/tests/test_backoffice_ca_truthflow_static.py::test_backoffice_screening_disposition_history_surfaces_evidence_reference \
  arie-backend/tests/test_case_command_centre_runtime.py::TestCaseCommandCentreRuntime::test_screening_review_blocker_uses_application_screening_reviews \
  arie-backend/tests/test_case_command_centre_runtime.py::TestCaseCommandCentreRuntime::test_uncleared_terminal_match_still_blocks_case_command_centre \
  arie-backend/tests/test_case_command_centre_runtime.py::TestCaseCommandCentreRuntime::test_non_review_screening_gate_blocker_uses_generic_copy \
  arie-backend/tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_inline_screening_controls_render_for_unresolved_hit \
  arie-backend/tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_resolved_hit_is_rendered_read_only \
  arie-backend/tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_screening_queue_dirty_flag_forces_refetch \
  arie-backend/tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_provider_identifiers_are_collapsed_under_technical_details \
  arie-backend/tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_screening_triage_cockpit_orders_subjects_and_focuses_without_mutation \
  arie-backend/tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_application_detail_screening_review_does_not_depend_on_queue_cache \
  arie-backend/tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_activity_log_formats_screening_reviews_for_officers
```

Result:

```text
Initial PR run: 19 passed in 2.49s
Post-CodeRabbit follow-up run: 22 passed in 2.49s
```

## CA1/CA2/CA3 Regression Subset

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_provider_label_policy.py \
  arie-backend/tests/test_screening_adapter_complyadvantage.py \
  arie-backend/tests/test_screening_complyadvantage_normalizer.py \
  arie-backend/tests/test_complyadvantage_evidence_audit.py \
  arie-backend/tests/test_complyadvantage_evidence_backfill.py \
  arie-backend/tests/test_complyadvantage_runtime_e2e.py \
  arie-backend/tests/test_screening_queue_state_integrity.py \
  arie-backend/tests/test_screening_state_priority_a.py \
  arie-backend/tests/test_inline_screening_runtime.py \
  arie-backend/tests/test_backoffice_ca_truthflow_static.py \
  arie-backend/tests/test_case_command_centre_runtime.py
```

Result:

```text
Initial PR run: 220 passed in 5.65s
Post-CodeRabbit follow-up run: 220 passed in 5.15s
```

## CodeRabbit Follow-Up

CodeRabbit completed review on PR #495 and raised comments in PR-CA4 scope. The follow-up commit addresses:

- `zip(..., strict=True)` for the evidence rollup invariant.
- Treating `Unclassified Provider Risk` as a displayable category context.
- Separating cleared provider decisions from current risks.
- Recognizing modern and legacy terminal disposition labels in unresolved-count rollups.
- Mapping additional internal screening status keys to officer-readable labels.
- Mapping legacy `true_match` audit labels to `Confirmed True Match`.
- Preserving `source_url` from monitoring source references.
