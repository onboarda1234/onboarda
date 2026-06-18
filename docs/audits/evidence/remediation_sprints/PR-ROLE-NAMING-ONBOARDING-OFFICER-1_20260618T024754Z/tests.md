# PR-ROLE-NAMING-ONBOARDING-OFFICER-1 Tests

Timestamp: 2026-06-18T02:47:54Z

## Passed

```bash
python3.11 -m pytest \
  arie-backend/tests/test_role_naming_onboarding_officer_static.py \
  arie-backend/tests/test_backoffice_ca_truthflow_static.py \
  arie-backend/tests/test_directors_ubos_report_ui_static.py \
  arie-backend/tests/test_backoffice_login_resilience_runtime.py \
  arie-backend/tests/test_backoffice_kpi_runtime.py \
  arie-backend/tests/test_idv_approval_gate.py
```

Result: `59 passed in 5.03s`

```bash
python3.11 -m pytest \
  arie-backend/tests/test_api.py::TestGovernanceAttemptAudit::test_co_pre_approval_decision_is_role_blocked_with_specific_403 \
  arie-backend/tests/test_api.py::TestGovernanceAttemptAudit::test_co_assignment_is_role_blocked_with_specific_403 \
  arie-backend/tests/test_api.py::TestGovernanceAttemptAudit::test_admin_can_assign_preapproval_review_application_and_audit_it \
  arie-backend/tests/test_ex12_client_security.py::TestRegression_BackendAuthority::test_roles_permissions_endpoint_returns_matrix \
  arie-backend/tests/test_ex12_client_security.py::TestRegression_BackendAuthority::test_role_permission_matrix_structure
```

Result: `5 passed in 2.97s`

```bash
python3.11 -m py_compile arie-backend/server.py
git diff --check
```

Result: passed.

## Expected Environment Note

System `python3` is Python 3.9.6 and cannot import this repo's Python 3.11+ syntax in some modules. The repo declares `requires-python = ">=3.11"` in `arie-backend/pyproject.toml`, so validation was rerun with `python3.11`.

Initial command under system Python 3.9:

```bash
python3 -m pytest arie-backend/tests/test_role_naming_onboarding_officer_static.py ... arie-backend/tests/test_idv_approval_gate.py
```

Result: 58 passed, 1 failed during collection/import of repo code with `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`. This was an interpreter mismatch, not a product test failure.

## Coverage Notes

- `co` displays as `Onboarding Officer`: covered by `test_role_display_mapping_keeps_internal_keys_and_sco_label`, user-management checks, and API error-copy checks.
- `sco` displays as `Senior Compliance Officer`: covered by `test_role_display_mapping_keeps_internal_keys_and_sco_label`.
- Permissions for `co` unchanged: covered by `test_role_permission_matrix_keys_and_gates_are_unchanged` and `test_role_permission_matrix_structure`.
- Approval gate behavior unchanged: covered by API tests for blocked CO pre-approval and assignment, plus static gate predicate checks.
- Role matrix behavior unchanged: covered by `test_roles_permissions_endpoint_returns_matrix`, `test_role_permission_matrix_structure`, and static matrix assertions.
- Audit display label works: covered by `test_audit_role_display_resolves_co_without_rewriting_records`.
- User management display label works: covered by `test_user_management_and_assignment_labels_use_onboarding_officer`.

## Not Run

- Browser smoke was not run for this pre-merge draft PR evidence set.
- Staging API smoke was not run because this branch is not merged to main and not deployed.
