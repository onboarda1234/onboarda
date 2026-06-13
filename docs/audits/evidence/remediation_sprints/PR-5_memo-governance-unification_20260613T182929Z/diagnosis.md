# PR-5 Diagnosis - Memo Governance Unification

## Scope

- Remediation IDs: FSI-005, FSI-006
- Branch: `codex/pr5-memo-governance-unification`
- Base `origin/main` SHA: `3a79bd014cc84e8189e50daeecc325c3cb9af0a5`
- PR-4 / FSI-007 status before start: CLOSED with merged-main staging evidence at `3a79bd014cc84e8189e50daeecc325c3cb9af0a5`.

## FSI-005 - Latest Compliance Memo Selection Is Inconsistent

Confirmed open on current main by code inspection.

Findings:

- `ApplicationDetailHandler` selected latest onboarding memo directly from `compliance_memos`.
- `ApprovalGateValidator` selected latest memo through direct SQL ordered by `created_at DESC, id DESC`.
- Evidence pack export loaded the memo through direct SQL ordered by `version DESC, id DESC`.
- EDD memo linkage loaded the latest onboarding memo through direct SQL ordered by `created_at DESC, id DESC`.
- Memo validation, supervisor, approval, application detail, decision gate, and export paths could disagree when version, id, and creation time diverged.
- API responses did not consistently expose the selected canonical memo ID or selector metadata.

## FSI-006 - Memo Approval UI Cannot Submit Required Approval Reason

Confirmed open on current main by code inspection.

Findings:

- Backend had existing special-case approval reason concepts for `pass_with_fixes` and supervisor warnings, but normal approval did not require one consistently.
- The memo UI rendered blocker text stating that the UI did not capture or submit `approval_reason`.
- `approveMemo()` sent only:

```json
{
  "officer_signoff": {
    "acknowledged": true,
    "scope": "memo",
    "source_context": "ai_advisory"
  }
}
```

- The UI could show approval affordances while the officer had no field to enter the mandatory reason.
- Validation UI could display a failed validation state with "No issues found" if the failed response had no issue details.

## Evidence

- Code inspection of:
  - `arie-backend/server.py`
  - `arie-backend/security_hardening.py`
  - `arie-backend/evidence_pack_export.py`
  - `arie-backend/edd_memo_integration.py`
  - `arie-backoffice.html`
- Regression tests added in `arie-backend/tests/test_pr5_memo_governance.py`.
