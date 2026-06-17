# Audit Reconstruction

Status: implementation tests passed; staging audit-trail screenshot pending.

Successful reassignment now persists a `Reassign` audit row with structured JSON in `detail` and before/after snapshots:

- `application_id`
- `application_ref`
- `previous_assignee_id`
- `previous_assignee_name`
- `new_assignee_id`
- `new_assignee_name`
- `actor_user_id`
- `actor_email`
- `actor_role`
- `reassignment_reason`
- `timestamp`
- `source_surface`

Reconstruction proof from automated tests:

- `TestGovernanceAttemptAudit::test_admin_can_assign_preapproval_review_application_and_audit_it` reconstructs who reassigned, from whom, to whom, why, when, and from which surface using structured fields instead of free text.
- `TestGovernanceAttemptAudit::test_reassignment_empty_reason_returns_400_and_does_not_persist` proves empty reason is rejected with no reassignment and no `Reassign` audit row.
- `TestGovernanceAttemptAudit::test_reassignment_whitespace_reason_returns_400_and_does_not_persist` proves whitespace reason is rejected with no reassignment and no `Reassign` audit row.
- `TestAdminPilotMutationAuditabilityAndRBAC::test_analyst_risk_config_mutation_returns_403_and_is_audited` proves unauthorized risk config mutation attempts are blocked and audit logged.
- `TestAdminPilotMutationAuditabilityAndRBAC::test_analyst_ai_config_mutation_returns_403_and_is_audited` proves unauthorized AI config mutation attempts are blocked and audit logged.
