# PR-CA4 Browser Smoke

Status: pending for merged-main staging validation.

Frontend files were changed in PR-CA4. Static/runtime tests exercise the affected officer-facing rendering paths, but authenticated staging browser smoke must still be run after merge and deployment.

Required browser smoke:

- Login as permitted officer/admin.
- Open Screening Queue.
- Open Application Screening Review.
- Confirm the screening summary is compact and readable.
- Confirm subject-level status is clear.
- Confirm adverse media is visible and understandable when present.
- Confirm provider references are available through collapsed/detail controls rather than cluttering the default row.
- Confirm disposition labels are business-readable:
  - Clear as False Positive
  - Confirm True Match
  - Escalate
  - Request More Information
- Confirm approval blockers use plain English.
- Confirm technical reason codes are not shown in default officer view.
- Confirm no console/network errors.
- Save screenshots to this evidence folder.

No browser smoke is claimed before merge/deploy.
