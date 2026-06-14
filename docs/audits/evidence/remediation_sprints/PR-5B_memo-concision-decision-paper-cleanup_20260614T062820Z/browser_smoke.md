# Browser Smoke

Branch-stage browser smoke for the original PR-5B branch was not run because it
did not modify frontend files.

Post-merge staging browser smoke then found a real defect in the back-office
memo view. Corrective branch browser smoke was run against the real
`arie-backoffice.html` renderer over local HTTP with a generated PR-5B memo
fixture.

Corrective branch result:

- `runtime_json/pr5b_corrective_local_browser_smoke.json` - pass
- `screenshots/pr5b_corrective_local_memo_panel.png`

Checks passed:

- Memo governance summary uses canonical blockers.
- LOW canonical risk score renders as LOW in memo text.
- `HIGH risk with score 22/100` is absent.
- Decision snapshot no longer says `Open blockers: None` when blockers exist.
- Validation panel does not show `No issues found` for non-clean/blocked state.
- Full Memo / Diagnostics remains accessible and collapsed by default.
- No console errors or failed requests in the local HTTP browser harness.

Required post-merge staging browser smoke remains pending:

- Back-office login.
- Open application memo section.
- Confirm concise decision-first memo display.
- Confirm no contradictory recommendation appears.
- Confirm long-form detail / diagnostics remain accessible.
- Confirm no messy officer-note/test artifact appears.
- Confirm no console/network errors.
- Client portal regression: no internal memo/gate/audit/supervisor leak.
