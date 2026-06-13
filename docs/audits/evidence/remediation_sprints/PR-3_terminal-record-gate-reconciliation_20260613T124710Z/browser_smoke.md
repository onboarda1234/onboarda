# Browser Smoke

## Local Branch Browser Smoke

Browser tool note:

- The in-app Browser plugin was available in the session, but its required `node_repl js` control tool was not exposed by tool discovery.
- Fallback used local Playwright from the shell.

Command:

- Local Node/Playwright smoke against the real `arie-backoffice.html`, served by a temporary local HTTP server with `/api/config/environment` stubbed.

Result:

- PASS — terminal approved record context rendered.
- PASS — active non-terminal blocker state rendered.
- PASS — no page errors or console errors after local config endpoint was stubbed.

Evidence:

- `runtime_json/branch_browser_smoke_result.json`
- `screenshots/branch_browser_terminal_record_context.png`
- `screenshots/branch_browser_active_gate_blocked.png`

Assertions:

- Terminal approved record showed `Legacy evidence incomplete`.
- Terminal approved record showed `Current-state diagnostics only`.
- Terminal approved record showed `Not historical basis`.
- Terminal approved record did not show `Blocked — ... unresolved controls`.
- Active non-terminal record still showed `Blocked — 1 unresolved controls`.
- Active non-terminal record still rendered mandatory screening action.

Limitations:

- This is local branch browser evidence only.
- FSI-003 closure still requires merged-main staging deployment, staging `/api/version` alignment, staging API smoke, and staging browser smoke.
