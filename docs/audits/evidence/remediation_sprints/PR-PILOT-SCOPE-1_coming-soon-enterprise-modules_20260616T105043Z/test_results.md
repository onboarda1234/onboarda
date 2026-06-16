# Test Results

## Focused Local Tests

Command:

```bash
/opt/homebrew/bin/python3.11 -m pytest \
  tests/test_inline_screening_runtime.py::TestInlineScreeningRuntime::test_screening_queue_sidebar_alias_routes_to_screening_renderer \
  tests/test_pilot_scope_enterprise_modules_static.py \
  tests/test_backoffice_monitoring_navigation_static.py \
  -q
```

Result:

`14 passed in 0.34s`

## Covered Assertions

- Regulatory Intelligence sidebar item is visible and marked Coming Soon/Enterprise.
- AI Compliance Supervisor navigation is marked Enterprise.
- Supervisor Audit Chain direct access is no longer blocked by the SCO-only operational guard.
- Regulatory Intelligence, Supervisor Dashboard, and Supervisor Audit render Coming Soon placeholders.
- Regulatory Intelligence operational preload is bypassed.
- Direct back-office route aliases are registered.
- Agent 8, Agent 9, and Agent 10 are marked Coming Soon/Enterprise/Not active in pilot.
- Agent 8/9/10 operational controls are disabled or hidden.
- Monitoring run controls do not run enterprise roadmap agents.
- Screening Queue sidebar alias regression remains intact.
