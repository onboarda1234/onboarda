# Applied migration report

Status: **NOT APPLIED**

The controlled apply is deliberately separated from code review, merge,
deployment, and staging dry-run validation.

Current mutation count: **0**

Current migration audit-entry count: **0**

The apply command requires all of:

1. the exact approved manifest version and canonical manifest SHA-256;
2. the exact locked-transaction dry-run fingerprint;
3. an explicit operator identity;
4. the exact founder phrase `APPROVE STAGING STATUS BACKFILL`;
5. every database, linkage, count, fingerprint, audit-writer, and feature-flag
   precondition to pass again inside one serializable transaction.

The mutation command also requires PostgreSQL; SQLite cannot execute this
approved staging manifest.

This report must be replaced with the actual transaction result only after the
founder approval gate is satisfied. Merge or deployment does not run the
backfill automatically.
