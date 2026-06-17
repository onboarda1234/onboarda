# Browser Smoke

Status: pending staging deployment.

Required browser checks after staging deploy:
- Create RMI request for missing Shareholder Register.
- Upload replacement from client portal or back office.
- Confirm replacement appears in the correct KYC document/evidence context.
- Officer accepts/reviews replacement.
- Officer continues from `rmi_sent`.
- KYC/memo flow is no longer dead-ended.
- Memo no longer reports Shareholder Register missing after valid replacement.
- A/B/C document classification remains preserved.
- Browser console and network show no blocking errors.

Local static check completed:
- Parsed the edited `renderRMIPanel` / `continueRMIReview` JavaScript block with Node: passed.

Screenshots: pending authenticated staging smoke.
