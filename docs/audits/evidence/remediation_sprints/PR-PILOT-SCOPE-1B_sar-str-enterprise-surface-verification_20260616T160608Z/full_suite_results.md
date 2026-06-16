# Full Suite Results

Command:
`/opt/homebrew/bin/pytest arie-backend/tests --ignore=arie-backend/tests/test_complyadvantage_runtime_e2e.py --ignore=arie-backend/tests/test_pr13_lifecycle_e2e_report.py --ignore=arie-backend/tests/test_staging_e2e.py -q`

Result:
- 5458 passed
- 17 skipped
- Duration: 233.06s / 3:53

Excluded by instruction:
- `arie-backend/tests/test_complyadvantage_runtime_e2e.py`
- `arie-backend/tests/test_pr13_lifecycle_e2e_report.py`
- `arie-backend/tests/test_staging_e2e.py`

Rationale:
- User explicitly instructed not to rerun the full ten-scenario E2E in this PR.

