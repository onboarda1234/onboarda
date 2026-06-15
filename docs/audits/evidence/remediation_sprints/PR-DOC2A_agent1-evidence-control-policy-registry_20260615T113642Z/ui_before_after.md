# UI Before / After

## Before

- Agent 1 settings behaved like a flat verification-check editor.
- Application Review verification results mixed material findings with routine passed technical checks.
- Officer-facing labels included weaker/internal terminology such as `Overall Result`, `Warnings`, and pilot-specific evidence classification language.
- Routine green technical passes could dominate the first-read experience.

## After

- Agent 1 settings render as a lifecycle-wide policy registry with summary stats, filters, lifecycle tabs, and collapsible document-family policy cards.
- Application Review document cards are decision-first:
  - Document name and file metadata.
  - Evidence Classification.
  - Lifecycle context.
  - Policy ID/version.
  - Dominant reliance status badge.
  - Required action.
  - Material issues before technical diagnostics.
- Routine passed technical checks are hidden by default.
- Failed/warning technical checks remain visible as material issues.
- Full technical/audit details remain available through expansion.

## Screenshots

- `screenshots/agent1_policy_registry_default.png`
- `screenshots/agent1_policy_registry_edd_sow_filter.png`
- `screenshots/agent1_policy_registry_change_management.png`
- `screenshots/agent1_policy_registry_unknown_unclassified.png`
- `screenshots/application_review_evidence_control_card.png`
- `screenshots/application_review_technical_details_expanded.png`
