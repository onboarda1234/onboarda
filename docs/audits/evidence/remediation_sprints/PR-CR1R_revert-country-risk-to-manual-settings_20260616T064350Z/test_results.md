# PR-CR1R Test Results

## Syntax / Static

Command:

```bash
python3 -m py_compile arie-backend/rule_engine.py arie-backend/memo_handler.py arie-backend/server.py arie-backend/db.py arie-backend/country_risk.py
git diff --check
```

Result: passed.

## Focused Tests

Command:

```bash
cd arie-backend
pytest -q tests/test_country_risk_governance.py tests/test_country_risk_governance_ui_static.py tests/test_api.py::TestRiskModelAdminConfigSafety::test_country_risk_endpoint_exposes_manual_settings_source tests/test_api.py::TestRiskModelAdminConfigSafety::test_grouped_manual_country_payload_is_saved_as_score_map tests/test_api.py::TestRiskModelAdminConfigSafety::test_partial_score_update_preserves_dimensions_and_thresholds tests/test_phase3_memo_integrity.py::test_memo_has_deterministic_risk_evidence_and_no_false_adverse_clear tests/test_risk_recomputation.py::TestRecomputeRiskHelper::test_recompute_sets_config_version tests/test_risk_elevation.py::TestPakistanElevation
```

Result on original PR head: `20 passed in 1.97s`.

Result after rebasing onto current `origin/main` `5d30ab0b4af83b8d6272fda1840e25e985c92037`: `20 passed in 3.91s`.

Follow-up corrective branch `codex/pr-cr1r-manual-country-defaults` after staging smoke found persisted manual map missing Mauritius:

```bash
python3 -m py_compile arie-backend/db.py arie-backend/tests/test_api.py
git diff --check
cd arie-backend
pytest -q tests/test_risk_config_integrity.py::TestSeededConfigShape::test_existing_country_scores_backfill_missing_manual_defaults tests/test_risk_config_integrity.py::TestSeededConfigShape::test_pr_cr1r_manual_defaults_repair_runs_once tests/test_risk_config_integrity.py::TestSeededConfigShape::test_seeded_country_risk_scores_is_dict tests/test_country_risk_governance.py tests/test_api.py::TestRiskModelAdminConfigSafety::test_country_risk_endpoint_exposes_manual_settings_source
```

Result: `10 passed in 1.77s`.

## Relevant Risk / Memo Suite

Command:

```bash
cd arie-backend
pytest -q tests/test_risk_scoring.py tests/test_risk_config_integrity.py tests/test_risk_config_shape.py tests/test_risk_elevation.py tests/test_risk.py tests/test_wave1_remediation.py tests/test_phase3_memo_integrity.py tests/test_risk_recomputation.py
```

Result on original PR head: `304 passed in 2.43s`.

Result after rebasing onto current `origin/main` `5d30ab0b4af83b8d6272fda1840e25e985c92037`: `304 passed in 3.65s`.

Follow-up corrective branch broader risk/memo/API slice:

```bash
cd arie-backend
pytest -q tests/test_risk_config_integrity.py tests/test_country_risk_governance.py tests/test_country_risk_governance_ui_static.py tests/test_api.py::TestRiskModelAdminConfigSafety tests/test_risk_scoring.py tests/test_risk_elevation.py tests/test_risk_recomputation.py tests/test_phase3_memo_integrity.py
```

Result: `249 passed in 3.30s`.

Migration-chain guard after moving the one-time repair marker out of `schema_version`:

```bash
cd arie-backend
pytest -q tests/test_fresh_install_pg_chain.py::test_fresh_sqlite_init_and_migration_chain tests/test_migration_chain_full.py::test_fresh_db_applies_all_migrations tests/test_risk_config_integrity.py::TestSeededConfigShape::test_pr_cr1r_manual_defaults_repair_runs_once
```

Result: `3 passed in 0.79s`.
