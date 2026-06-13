# Test Results

## Local Targeted Validation

Environment: local branch `codex/pr3-terminal-record-gate-reconciliation`, Python 3.11.15.

Commands and results:

- `git diff --check`
  - PASS
- `python3.11 -m py_compile arie-backend/server.py arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py`
  - PASS
- `python3.11 -m pytest arie-backend/tests/test_pr3_terminal_record_gate_reconciliation.py -q`
  - PASS — 7 passed
- `python3.11 -m pytest arie-backend/tests/test_case_command_centre_runtime.py -q`
  - PASS — 28 passed
- `python3.11 -m pytest arie-backend/tests/test_approval_gate.py arie-backend/tests/test_decision_model.py -q`
  - PASS — 59 passed

## FSI-001 Regression

- `python3.11 -m pytest arie-backend/tests/test_pr1_client_api_boundary.py arie-backend/tests/test_pr1b_client_notification_boundary.py -q`
  - PASS — 9 passed

Coverage:

- client denied from internal application/screening surfaces
- client notifications remain sanitized
- client RMI/application detail remains sanitized

## FSI-002 Regression

- `python3.11 -m pytest arie-backend/tests/test_sprint35.py -q`
  - PASS — 40 passed

Coverage:

- bearer and cookie logout revocation
- replay protection
- normal login after logout

## Notes

- Initial `python3 -m pytest ...` used system Python 3.9.6 and failed before PR-3 logic because existing repository code uses Python 3.10+ union type syntax in `screening_config.py`.
- All authoritative local test runs used Python 3.11.15, matching CI.
