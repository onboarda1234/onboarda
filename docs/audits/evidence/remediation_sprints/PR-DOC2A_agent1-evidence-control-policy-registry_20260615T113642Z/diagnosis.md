# PR-DOC2A Diagnosis

Base `origin/main` SHA: `6e44c13d79066fa4751cf2050e61bc009d7f9356`

Branch: `codex/pr-doc2a-agent1-evidence-control-policy-registry`

## Current-State Gap

Agent 1 was presented primarily as an onboarding verification-check configuration screen. The settings surface did not make lifecycle-wide evidence coverage visible for EDD, change management, periodic review, monitoring/SAR, or regulatory/resource evidence.

Application Review document cards also exposed routine passed technical controls in the main verification result flow. File-format, file-size, duplicate, and hash-style controls are audit-relevant, but they created officer noise when they passed and diluted attention from material reliance issues.

## Diagnosed UX Risk

The officer-facing UI did not consistently answer the decision questions first:

- What evidence is this?
- What policy applies?
- Can the officer rely on it?
- If not, what action is required?
- What material issues require review?
- Where are technical/audit details?

## Runtime Compatibility Finding

Local API smoke against an existing demo SQLite database initially failed `GET /api/applications` with:

`sqlite3.OperationalError: no such column: uploaded_by`

The code change added document upload attribution to API payloads, but the existing `documents` table migration path did not add `uploaded_by`. This was fixed by adding the column to the canonical documents schema plus an idempotent migration helper.

## Scope Boundary

This PR is a foundation and UX/control PR only. It does not implement CA/PR-PROV1, CR/country-risk, PR-7, post-approval locking, broad change-management enforcement, or autonomous approval/rejection/waiver behavior.
