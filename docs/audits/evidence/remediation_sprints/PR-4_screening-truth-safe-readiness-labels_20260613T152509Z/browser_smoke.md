# Browser Smoke

No live browser smoke completed at branch stage.

Branch-level JavaScript runtime coverage:

```bash
PYTHONPATH=arie-backend pytest -q arie-backend/tests/test_backoffice_ca_truthflow_static.py arie-backend/tests/test_case_command_centre_runtime.py
```

Result:

```text
Static/runtime back-office screening and Case Command Centre tests included in the 116-test targeted pass.
```

Required after merged-main staging deployment:

- Login as permitted back-office user.
- Open Screening Queue.
- Open application with unresolved screening blockers/hits.
- Confirm no unsafe approval-ready wording appears.
- Confirm blocker reasons are clear.
- Confirm screening terminal/provider clear/defensible clear/gate readiness are visually distinct.
- Open Application Review memo/gate areas where screening status appears.
- Confirm no contradictory copy.
- Run client portal smoke to confirm own portal-safe application still loads and internal screening/gate data does not leak.
