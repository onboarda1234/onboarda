# PR-PROV1 Runtime Test Cases

## Status

BLOCKED / NEEDS EVIDENCE.

Operator approval was provided for controlled runtime screening, but the test
matrix was not started because dashboard/account mode was not independently
confirmed as Production after prior dashboard evidence reportedly showed
Sandbox.

## Approved Subjects

Only these subjects may be used:

| Subject Type | Approved Subject | Notes |
|---|---|---|
| Entity | `Multigate Technologies Limited` | Use minimum entity identifiers required for screening. |
| Director | `Stephen Margolis` | No DOB/passport/address unless separately approved and strictly necessary. |
| UBO | `Sir Michael Lawrence Davis` | User separately mentioned `Micheal Davis`; use the approved full name spelling unless operator confirms otherwise. DOB was provided but should not be sent unless strictly necessary. |
| Intermediary | `Gemrock UK Plc` | Use minimum entity identifiers required for screening. |

## Approved Caps

- Maximum screening cases total: `10`
- Maximum expected CA usage/cost exposure: `USD 50`
- Screening requests actually sent in this resumed run: `0`

## Required Matrix After Mode Confirmation

| Test | Status | Evidence |
|---|---|---|
| Provider activation/status | READ-ONLY PASS | `runtime_json/post_approval_preflight_redacted.json` |
| Entity screening | NOT RUN | blocked by dashboard/account-mode confirmation |
| Director screening | NOT RUN | blocked by dashboard/account-mode confirmation |
| UBO screening | NOT RUN | blocked by dashboard/account-mode confirmation |
| Intermediary screening | NOT RUN | blocked by dashboard/account-mode confirmation |
| Webhook receipt/signature/dedupe | NOT RUN | no provider event without runtime screening |
| Screening Queue status | NOT RUN | no controlled case created |
| Screening Review detail evidence | NOT RUN | no controlled case created |
| Memo impact/staleness | NOT RUN | no controlled case created |
| Approval gate behaviour | NOT RUN | no controlled case created |
| Audit trail evidence | NOT RUN | no controlled case created |
| Browser smoke | NOT RUN | no controlled case created |

## Stop Condition Applied

Runtime screening was stopped before any provider request because production
provider mode was not fully proven at the dashboard/account level. This
preserves the approved case and billing caps and avoids uncontrolled Sandbox vs
Production ambiguity.
