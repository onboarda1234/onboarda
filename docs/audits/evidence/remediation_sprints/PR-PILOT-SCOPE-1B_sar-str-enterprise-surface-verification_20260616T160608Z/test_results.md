# Test Results

Focused PR tests:

Command:
`/opt/homebrew/bin/pytest arie-backend/tests/test_feature_flags.py arie-backend/tests/test_pilot_scope_enterprise_modules_static.py arie-backend/tests/test_pilot_scope_backend_lockdown.py -q`

Result:
- 25 passed in 10.64s

Additional regression clusters:

Command:
`/opt/homebrew/bin/pytest arie-backend/tests/test_audit_export.py arie-backend/tests/test_case_command_centre_runtime.py -q`

Result:
- 64 passed in 17.21s

Command:
`/opt/homebrew/bin/pytest arie-backend/tests/test_ex11_ai_advisory_labels.py arie-backend/tests/test_pilot_scope_backend_lockdown.py -q`

Result:
- 55 passed in 3.62s

Note:
- Local `/usr/bin/python3` is Python 3.9 and cannot import backend files using Python 3.10+ union syntax.
- Test runs used `/opt/homebrew/bin/pytest`, which runs under Python 3.11.15.

