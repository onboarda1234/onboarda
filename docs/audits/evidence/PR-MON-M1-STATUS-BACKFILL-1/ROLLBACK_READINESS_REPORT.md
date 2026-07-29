# Rollback readiness report

Rollback is narrow and audit-driven. It never executes a broad status reset.

For alert 583, rollback is eligible only when:

- status still equals `routed_to_edd`;
- exactly one `monitoring.alert.status_backfilled` audit row exists for
  manifest `1.0.0`;
- no rollback audit already exists;
- no later audit entry exists for `monitoring_alert:583`;
- all normalized `monitoring_alerts` fields still match the PR #900 baseline;
- all four Monitoring feature flags remain OFF;
- the approved rollback-plan fingerprint matches the locked transaction plan.

Rollback locks both `monitoring_alerts` and `audit_log` before establishing its
serializable snapshot. This blocks canonical and legacy audit inserts until the
later-audit check, status restoration, and rollback audit append complete.

If any officer or workflow changes the row after migration, automated rollback
fails closed. A successful rollback changes only alert 583 from
`routed_to_edd` to its recorded before-state `in_review` and appends a new
hash-chained rollback audit entry. It does not delete or alter the original
migration audit.

## Validation

Focused SQLite and PostgreSQL tests prove:

- rollback planning identifies only the recorded changed row;
- a later alert audit makes rollback ineligible;
- successful rollback leaves canonical and manual-review rows untouched;
- the original migration audit remains present;
- a separate rollback audit is appended.

The runtime rollback fingerprint is intentionally not generated until an
approved apply exists.
