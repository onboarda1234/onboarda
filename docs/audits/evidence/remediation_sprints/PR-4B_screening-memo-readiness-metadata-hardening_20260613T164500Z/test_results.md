# Test Results

Branch: `codex/pr4b-screening-memo-readiness-metadata-hardening`

Commands:

```bash
git diff --check
```

Result: passed.

```bash
python3 -m py_compile arie-backend/screening_state.py arie-backend/server.py arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py
```

Result: passed.

```bash
PYTHONPATH=arie-backend pytest -q \
  arie-backend/tests/test_pr4_screening_memo_readiness_metadata.py \
  arie-backend/tests/test_screening_state_priority_a.py \
  arie-backend/tests/test_backoffice_ca_truthflow_static.py \
  arie-backend/tests/test_case_command_centre_runtime.py
```

Initial result: `108 passed in 4.34s`.
After CodeRabbit follow-up: `109 passed in 4.53s`.

```bash
PYTHONPATH=arie-backend pytest -q \
  arie-backend/tests/test_pr1_client_api_boundary.py \
  arie-backend/tests/test_pr1b_client_notification_boundary.py \
  arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py \
  arie-backend/tests/test_sprint35.py::TestLogout
```

Initial result: `29 passed in 7.80s`.
After CodeRabbit follow-up: `29 passed in 7.43s`.
