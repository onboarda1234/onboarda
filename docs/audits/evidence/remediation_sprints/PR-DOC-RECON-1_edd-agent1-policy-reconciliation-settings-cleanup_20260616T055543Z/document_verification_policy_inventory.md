# Document Verification Policy Inventory

Sources inspected:

- `arie-backoffice.html`
- `arie-backend/db.py`
- `arie-backend/verification_matrix.py`
- `arie-backend/tests/test_pr_doc_recon1_policy_reconciliation.py`

## Settings Cleanup Result

The Document Verification Policies page now shows:

- Title: `Document Verification Policies`
- Primary section: `Underlying Verification Check Configuration`
- Tabs:
  - Entity Documents
  - Person / KYC Documents
  - Enhanced / EDD Documents

The page no longer shows the prominent registry/dashboard UI:

- `Agent 1 Evidence Control Layer`
- `Document Policy Registry`
- `Canonical Policy Coverage`
- top metric cards for total/active/manual/future policy counts
- lifecycle/gate/status registry filters

## Editable Check Configuration

The settings editor still persists through the existing `/config/verification-checks` endpoint.

EDD/enhanced check configuration is separated in the UI by splitting supported EDD document types out of the existing entity payload:

- `bankref`
- `source_wealth`
- `source_funds`
- `bank_statements`
- `fin_stmt`
- `aml_policy`
- `contracts`

This avoids changing the API contract while making EDD checks visible in a simple admin configuration section.

## Canonical Registry Data

The canonical policy data remains in the backend/API summary for auditability and consistency. It is no longer presented as a top-level architecture dashboard in the settings UI.
