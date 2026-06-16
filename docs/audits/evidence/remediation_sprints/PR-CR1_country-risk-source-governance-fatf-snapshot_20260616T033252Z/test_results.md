# Test Results

## Targeted PR-CR1 Tests

Command:

```bash
cd /Users/Aisha/Projects/onboarda-pr-cr1/arie-backend
pytest -q tests/test_country_risk_governance.py tests/test_country_risk_governance_ui_static.py tests/test_phase3_memo_integrity.py::test_memo_has_deterministic_risk_evidence_and_no_false_adverse_clear tests/test_api.py::TestRiskModelAdminConfigSafety::test_country_risk_endpoint_exposes_snapshot_provenance
```

Result:

```text
11 passed in 2.28s
```

## Relevant Risk Suite

Command:

```bash
pytest -q tests/test_risk_scoring.py tests/test_risk_config_integrity.py tests/test_risk_config_shape.py tests/test_risk_elevation.py tests/test_risk.py tests/test_wave1_remediation.py
```

Result:

```text
245 passed in 3.33s
```

## Affected Recompute Regression

Command:

```bash
pytest -q tests/test_risk_recomputation.py::TestRecomputeRiskHelper::test_recompute_sets_config_version
```

Result:

```text
1 passed in 1.11s
```

## Full Backend Suite

Command:

```bash
pytest -q
```

Result:

```text
5420 passed, 17 skipped in 245.28s (0:04:05)
```

## Static/Syntax Checks

Command:

```bash
python3 -m py_compile arie-backend/country_risk.py arie-backend/rule_engine.py arie-backend/memo_handler.py arie-backend/server.py arie-backend/db.py
git diff --check
```

Result: passed with no output.
