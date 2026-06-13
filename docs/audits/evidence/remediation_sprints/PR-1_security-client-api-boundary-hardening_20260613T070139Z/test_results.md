# PR-1 Targeted Test Results

## Initial Focused Regression

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest tests/test_pr1_client_api_boundary.py -q
```

Result:

```text
5 passed in 4.03s
```

## Broad Targeted Regression

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  tests/test_pr1_client_api_boundary.py \
  tests/test_auth.py \
  tests/test_r9_portal_ownership.py \
  tests/test_r10_portal_ownership.py \
  tests/test_screening_queue.py \
  tests/test_audit_export.py \
  tests/test_ex13_batch_refresh.py \
  tests/test_api.py::TestAuthenticatedAccess::test_applications_endpoint_excludes_fixtures_by_default_and_supports_alias_opt_in \
  tests/test_application_enhanced_requirements.py::test_applications_list_includes_enhanced_operational_summary_and_filters \
  -q
```

Result:

```text
118 passed in 9.19s
```

## Static / Compile Checks

Command:

```bash
git diff --check
```

Result:

```text
PASS
```

Command:

```bash
/opt/homebrew/bin/python3.11 -m py_compile \
  arie-backend/base_handler.py \
  arie-backend/server.py \
  arie-backend/tests/test_pr1_client_api_boundary.py \
  arie-backend/tests/test_auth.py \
  arie-backend/tests/test_api.py \
  arie-backend/tests/test_application_enhanced_requirements.py
```

Result:

```text
PASS
```
