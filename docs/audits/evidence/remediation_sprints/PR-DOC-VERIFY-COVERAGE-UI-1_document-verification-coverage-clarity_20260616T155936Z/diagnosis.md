# PR-DOC-VERIFY-COVERAGE-UI-1 Diagnosis

- Base `origin/main` SHA: `5b98eee3324c4bed33815c134afaf35dd009b7b1`
- Branch: `codex/pr-doc-verify-coverage-ui-1`
- Scope: back-office Application Review document verification UX only

## Problem observed

The Application Review document section still repeated the same verification problem multiple times across:

- default row chips,
- verification-details grid,
- issue banners,
- system-warning copy,
- full check list.

The staging screenshots showed the same file-access/system-warning defect being restated as:

- `System issue`
- `Document status`
- `Why review is required`
- `Issue`
- failed/warn check rows

That made it hard for an officer to answer:

- did Agent 1 actually run,
- which checks ran,
- which expected checks did not run,
- whether the failure was a document defect or a system-access problem.

## Root cause

The renderer combined two different concerns in one panel:

1. officer-facing decision guidance, and
2. full technical/audit payload rendering.

`buildVerificationResultsHtml()` was still emitting:

- advisory/system banners,
- status summaries,
- issue summaries,
- warning summaries,
- visible failed/warn check rows,
- and a nested full check list.

At the same time, `renderDocumentAuditDetails()` already rendered status, issue, blocker, policy, timestamp, and uploader fields. That created duplication by design.

## Fix direction

- Keep the row compact and decision-first.
- Keep the outer `Details` section audit-focused.
- Add one explicit verification-coverage summary.
- Move raw check payloads to a deeper collapsed `Technical audit details` section.
- Distinguish manual-review-only policy cases from runtime-executable policies.
- Show missing expected checks when the policy is runtime-executable but persisted checks do not cover the expected policy surface.
