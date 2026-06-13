# Full Suite Results

## Local Full Relevant Backend Suite

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  arie-backend/tests/test_auth.py \
  arie-backend/tests/test_auth_extended.py \
  arie-backend/tests/test_auth_stability.py \
  arie-backend/tests/test_sprint35.py \
  arie-backend/tests/test_pr1_client_api_boundary.py \
  arie-backend/tests/test_pr1b_client_notification_boundary.py \
  -q
```

Result:

```text
114 passed in 10.02s
```

## Whole-Repository Backend Suite

Not claimed locally in this evidence pack. Previous PR-1 work identified a
local macOS native dependency blocker around WeasyPrint/Pango CFFI import paths.
GitHub Actions is configured to run:

- `lint-and-test`: `pytest tests/ -v --tb=short --ignore=tests/test_pdf_generator.py`
- `pdf-tests`: installs Pango/native dependencies and runs
  `pytest tests/test_pdf_generator.py -v --tb=short`

Whole-repo CI evidence is pending until this PR is opened and checks complete.
FSI-002 must not be marked CLOSED without acceptable GitHub CI/full-suite
evidence plus merged-main staging proof.
