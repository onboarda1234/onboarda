# PR-DOC-UI-2 Diagnosis

Base `origin/main` SHA: `69f751cf12f7a7694ecfcd67ad5f6134c706f393`

Branch: `codex/pr-doc-ui-2-manus-style-compact-document-review`

## Current-State Finding

The Application Review `KYC Documents` renderer already grouped documents by action state and preserved the A/B/C/D/E/F/G review sections, but each uploaded document row still rendered as a tall card with a default `document-review-fields` grid.

Default rows exposed too much system and audit metadata:

- status field boxes
- issue field boxes
- blocker field boxes
- next action field boxes
- last verified
- uploaded by
- policy/audit-oriented phrasing in supporting logic
- large KYC tab AI advisory banner

This made the officer workflow more system-centric than action-centric.

## Scope Decision

This PR is UI-only. It does not change:

- portal upload flow
- portal UI
- async verification behavior
- document verification logic
- Agent 1 checks
- scoring
- SAR/STR inactive status
- change-management enforcement
- periodic-review enforcement
- approval/document gates

