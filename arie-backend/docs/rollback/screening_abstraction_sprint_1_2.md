# Screening Abstraction — Sprint 1–2 Rollback Playbook

**Tag:** `v5.0-pre-screening-abstraction`
**Scope:** ComplyAdvantage migration scaffolding (non-authoritative normalized storage)

---

## Flag-Off Procedure

### Development / Testing
```bash
export ENABLE_SCREENING_ABSTRACTION=false
# Or simply unset — defaults to false in all environments
unset ENABLE_SCREENING_ABSTRACTION
```

### Staging
1. Set `ENABLE_SCREENING_ABSTRACTION=false` in Render environment variables (or equivalent).
2. Restart the service.
3. Verify: no new rows appear in `screening_reports_normalized`.
4. Verify: existing screening flow unchanged (run a test submission).

### Production
1. Set `ENABLE_SCREENING_ABSTRACTION=false` in Render environment variables.
2. Restart the service.
3. Verify: screening submission flow unchanged.
4. Verify: no new rows in `screening_reports_normalized`.

### Expected Observable Behavior After Flag-Off
- Screening submissions proceed exactly as before.
- No normalized records are written.
- Existing normalized records remain in the database but are not read by any control.
- Memo generation, risk scoring, approval gates, and backoffice display are unaffected.
- All EX-01 through EX-13 controls remain operational with no change.

---

## Code Revert Decision Tree

```
Is the flag OFF and behavior is normal?
  ├── YES → No code revert needed. Flag-off is sufficient.
  │         Monitor for 48 hours, then decide on cleanup.
  └── NO  → Is there a bug in the screening abstraction code
            that affects runtime even with flag OFF?
              ├── YES → Revert to tag v5.0-pre-screening-abstraction
              │         git checkout v5.0-pre-screening-abstraction
              │         Deploy from that tag.
              └── NO  → Investigate further. The abstraction layer
                        should have zero effect when flag is OFF.
```

### When to Revert to Tag vs When to Disable the Flag
- **Disable the flag** when: the abstraction layer has a bug that only manifests when enabled (e.g., normalized write failure, performance degradation during dual-write).
- **Revert to tag** when: the abstraction code causes import errors, startup failures, test failures, or affects runtime behavior even with the flag disabled.

---

## Normalized Screening Records

### When to Retain
- If flag-off resolves the issue and you plan to re-enable after a fix.
- Records are non-authoritative and do not affect any control.

### When to Delete
- If reverting to tag and permanently abandoning the abstraction.
- If records contain corrupted data that could confuse future re-enablement.
- SQL: `DELETE FROM screening_reports_normalized;`

### When to Ignore
- In most cases. The `screening_reports_normalized` table is non-authoritative.
- No EX-validated control reads it. No backoffice display uses it.
- No memo, risk score, or approval decision depends on it.

---

## Audit-Log Implications of Rollback

- Flag-off: audit logs continue to record all screening operations normally. No audit gap.
- Code revert to tag: no audit entries are lost. The abstraction layer only adds new audit-adjacent records (normalized writes). Reverting removes the dual-write path but does not alter existing audit entries.
- The `screening_reports_normalized` table is explicitly excluded from audit before_state/after_state diffs (it is non-authoritative scaffolding).

---

## Staging Rollback Test Steps

1. **Pre-test:** Record a test application ID that has both a legacy `screening_report` and a `screening_reports_normalized` row.
2. **Flag off:** Set `ENABLE_SCREENING_ABSTRACTION=false` in staging environment.
3. **Restart:** Restart the staging service.
4. **Submit a new application** through the portal and trigger screening.
5. **Verify:**
   - Application screening completes normally.
   - `prescreening_data.screening_report` is populated correctly.
   - No new row appears in `screening_reports_normalized`.
   - Memo generation works.
   - Approval gates evaluate correctly.
   - Backoffice displays screening data correctly.
6. **Check existing data:** Confirm the pre-test application's legacy screening report is unchanged.
7. **Log review:** No errors related to screening abstraction in logs.

---

## Emergency Rollback Approval

**Contact:** Project lead or designated on-call compliance engineer.

If no specific contact is defined, follow the standard incident response process:
1. Disable the flag immediately (no approval needed for flag-off).
2. Notify the project lead within 1 hour.
3. If code revert is needed, obtain approval before deploying the reverted tag.

---

## Known Sprint 2 Limitations

### Webhook-Driven Drift

Sumsub webhook-triggered updates may mutate the legacy `screening_report` (in `prescreening_data`) after the initial normalization, leaving the `screening_reports_normalized` record stale.

**This is accepted only because:**
- Normalized storage is non-authoritative in Sprint 2.
- No EX-validated control reads it.
- Sprint 2 is migration scaffolding only.

**Sprint 3 must resolve webhook-driven drift** before any downstream consumer reads normalized records. Options include:
- Re-normalizing on webhook receipt.
- Listening for `prescreening_data` mutations and updating the normalized copy.
- Making webhook updates write to both stores atomically.

### No Scheduled Parity Job

The staging shadow-parity check is a manual script (`scripts/staging_shadow_parity.py`). No external scheduler (Celery, APScheduler, cron) exists in the current infrastructure. Automated scheduled execution requires infrastructure changes and is deferred to Sprint 3.

### No External Alerting

Parity failures are logged to stdout/stderr only. No external alerting mechanism (PagerDuty, Slack webhook, email) is wired. External alerting integration is deferred to Sprint 3.
