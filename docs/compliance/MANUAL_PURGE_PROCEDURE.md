# Manual Retention Purge Procedure (P12-8 / DCI-020)

**Scope:** retention categories the automatic scheduler explicitly does NOT
enforce: `client_pii`, `kyc_documents`, `screening_results`,
`compliance_memos`, `application_data`, `sar_reports`
(and the documentation-only `session_tokens`). The authoritative list and
per-category reasons live in `gdpr.py` `MANUAL_PURGE_CATEGORIES`.

**Why manual:** these categories anchor retention to relationship end /
decision date (not row age), reference physical artefacts (files, S3
objects), and constitute AML decision evidence subject to legal hold. An
unattended age sweep cannot make those judgements — audit finding B1 showed
what a mis-scoped automatic purge does to evidence. Subject-scoped erasure
belongs to the erasure engine (H2B), which remains wired-but-OFF pending the
PC-4 control pack.

**Automatic enforcement remains active** for `audit_logs` (manual-only by
B1 protection) and `monitoring_alerts` via the daily scheduler, with
enriched evidence logging (DCI-021).

---

## Procedure

1. **Trigger.** Quarterly retention review, or `get_expired_data_summary()`
   reporting `manual_purge_required` categories (each carries its policy's
   cutoff date).
2. **Scope.** The acting officer drafts a purge scope: category, the
   concrete records (subject ids / application ids / date range), the tables
   affected, and the retention basis (policy id, cutoff). Verify:
   - retention anchor (relationship end / decision date) is actually past
     for every record in scope;
   - no legal hold, open SAR linkage, ongoing investigation, or open DSAR
     touches any record in scope;
   - physical artefacts (uploaded files, S3 objects) are enumerated
     alongside DB rows.
3. **Approval.** SCO (or Admin acting as SCO) approves the scope in writing.
   The approver must be a different person from the operator where staffing
   allows. `sar_reports` additionally requires FIU-coordination sign-off.
4. **Execution.** Operator performs the deletion in a change window,
   capturing per-table deleted counts (and artefact counts).
   **Never** delete from `supervisor_audit_log` (tamper-evident chain) — any
   deletion there is an incident.
5. **Evidence (mandatory, same change window).** Record the purge in
   `data_purge_log` via the CLI:

   ```bash
   cd arie-backend
   python scripts/record_manual_purge.py \
     --category client_pii \
     --counts '{"clients": 3, "applications": 3, "directors": 7}' \
     --reason "Q3 retention review: relationships ended 2019-06, 7y elapsed" \
     --purged-by <operator user id> \
     --approved-by <SCO user id> \
     --subject-id <client id, if subject-scoped> \
     --application-id <application id, if single-application> \
     --evidence '{"change_ticket": "OPS-123", "artefacts_deleted": 12}'
   ```

   The CLI writes the same enriched evidence row the automatic engine
   writes: `purge_batch_id` (`manual-…`), `tables_affected`,
   `per_table_counts`, `subject_id`/`application_id`, and an
   `evidence_json` document naming the approver. It refuses never-purge
   tables and unknown categories.
6. **Verification.** A second person confirms the `data_purge_log` row
   exists (`SELECT * FROM data_purge_log WHERE purge_batch_id = '…'`) and
   matches the approved scope. File the approval + the batch id with the
   change ticket.

## Invariants

- `supervisor_audit_log` is never deleted by any path.
- `audit_log` may only be purged by a **deliberate manual**
  `purge_expired_data("audit_logs", dry_run=False)` — the unattended
  scheduler refuses it.
- A purge without its `data_purge_log` evidence row did not happen — the
  automatic engine enforces this atomically (delete + evidence in one
  transaction); this procedure enforces it for manual purges via step 5-6.
- Setting `auto_purge=TRUE` on a manual-with-procedure category is a
  misconfiguration; the scheduler logs an ERROR every run until corrected.
