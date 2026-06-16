# Agent 1 Check Inventory

Sources inspected:

- `arie-backend/db.py`
- `arie-backend/verification_matrix.py`
- `arie-backoffice.html`
- `/api/config/ai-agents` local smoke response
- `/api/config/verification-checks` local smoke response

## Agent 1 Scope After Cleanup

Agent 1 is named `Identity & Document Integrity Agent`.

Description:

> Agent 1 verifies uploaded onboarding and requested evidence documents using the checks configured in Document Verification Policies. It can verify, flag, block reliance, recommend officer action, and trigger required follow-up. It cannot approve, reject, waive, or perform sanctions/PEP/adverse-media screening.

## Runtime Check Counts

Local `/api/config/verification-checks` smoke on the disposable demo server returned:

- Entity rows: 16
- Person rows: 7
- EDD rows included in entity payload for the existing API contract: `aml_policy`, `bank_statements`, `bankref`, `contracts`, `fin_stmt`, `source_funds`, `source_wealth`
- Executable check instances: 88
- Method counts: Rule 45, Hybrid 35, AI 8, Manual 54, Unknown 0

## Important Scope Constraints

- Agent 1 does not perform sanctions, PEP, or adverse-media screening.
- SAR/STR remains inactive/future enterprise.
- Manual-review-only and future/enterprise policies are not counted or presented as active runtime verified in the simplified settings page.
