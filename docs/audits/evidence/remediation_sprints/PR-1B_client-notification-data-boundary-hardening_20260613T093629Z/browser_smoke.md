# PR-1B Browser Smoke Evidence

Not completed at branch stage.

Browser smoke is mandatory after PR-1B is merged and deployed because the failed PR-1 evidence came from the client portal notifications surface.

Required staging browser checks:

- Client portal login succeeds.
- Client opens notifications/status messages area.
- No `Officer notes`, internal notes, compliance rationale, memo, supervisor, gate, provider, audit, or internal-risk wording appears.
- No exact reproduced leaked phrases appear:
  - `Officer notes: testing of PEP`
  - `runtime audit`
  - `source of funds`
- Safe notification text appears or unsafe legacy items are safely suppressed/sanitized.
- Requested-document labels shown to the client remain useful and do not expose provider/audit/internal-risk wording.
- No console/network errors caused by notification sanitization.
- Back-office login succeeds.
- Application review still loads.
- Screening queue still loads.
- Normal back-office access remains intact.
