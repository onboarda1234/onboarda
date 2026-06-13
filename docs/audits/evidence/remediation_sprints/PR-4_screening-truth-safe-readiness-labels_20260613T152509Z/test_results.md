# Test Results

## Syntax / Diff

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
python3 -m py_compile arie-backend/screening_state.py arie-backend/memo_handler.py arie-backend/security_hardening.py arie-backend/tests/test_screening_state_priority_a.py arie-backend/tests/test_screening_clearance_validation_supervisor.py arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_case_command_centre_runtime.py
```

Result:

```text
PASS
```

## Targeted Screening / Memo / UI Tests

Command:

```bash
PYTHONPATH=arie-backend pytest -q arie-backend/tests/test_screening_state_priority_a.py arie-backend/tests/test_screening_clearance_validation_supervisor.py arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_case_command_centre_runtime.py
```

Result:

```text
116 passed in 3.19s
```

Frontend/static note:

```text
No configured frontend lint/build command exists. arie-backend/package.json contains only a placeholder test script. Back-office HTML/JS changes are covered by static and Node runtime tests in test_backoffice_ca_truthflow_static.py and test_case_command_centre_runtime.py.
```

## Prior Remediation Regression Subset

FSI-001 and FSI-003:

```bash
PYTHONPATH=arie-backend pytest -q arie-backend/tests/test_pr1_client_api_boundary.py arie-backend/tests/test_pr1b_client_notification_boundary.py arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py
```

Result:

```text
16 passed in 6.39s
```

FSI-002:

```bash
PYTHONPATH=arie-backend pytest -q arie-backend/tests/test_sprint35.py::TestLogout
```

Result:

```text
13 passed in 1.44s
```
