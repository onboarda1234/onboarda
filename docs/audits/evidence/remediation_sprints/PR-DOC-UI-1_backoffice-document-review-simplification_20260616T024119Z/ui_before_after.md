# UI Before / After

Before:

- Application Review document cards mixed decision information with technical/audit details.
- Routine technical checks and policy internals were visually prominent.
- Officers had to parse the card to determine action, blocker, and next step.
- Portal-slot documents could appear as classification problems when the expected slot type was already known.

After:

- Application Review documents are grouped by officer action:
  - Action required
  - Missing
  - Verified
  - Optional / additional
- Each row defaults to:
  - document name/file
  - expected document type
  - status
  - issue
  - blocker
  - next action
  - direct View/Download actions
- Details remain available for:
  - policy ID/version
  - agent run ID
  - evidence hash
  - material check results
  - technical/audit details
  - officer action history
- Routine technical/audit internals remain hidden by default.
- Portal-slot documents show the expected document type rather than `Unclassified`.
- `Approval blocked` styling uses warning/error colors, not green.

