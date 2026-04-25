# ADR-0008: Schema Migrations Policy

## Status

Accepted, 2026-04-25.

## Date

2026-04-25.

## Context

A2 and A4 added schema changes to `init_db()` only. Production databases that pre-date these changes do not receive them on startup. This creates a prod-resume gap and breaks the audit trail an external reviewer needs.

## Decision

Every schema change must include both an `init_db` update, for fresh installs, and a migration file, for long-lived databases. New migrations go in `arie-backend/migrations/scripts/`; inline `_run_migrations` is deprecated. The CI guard in `lint-and-test` enforces this for changes inside `db.py`'s `_get_postgres_schema()` and `_get_sqlite_schema()` functions.

## Consequences

- Positive: production databases can be brought up to current schema deterministically. Audit trail is complete. Investor diligence has a clean answer.
- Negative: 1–2 hours of extra work per phase. Tradeoff explicitly accepted.

## Documented exceptions

The following schema-init paths are pre-existing and out of scope for the policy enforcement. They will be addressed as deferred work:

- `arie-backend/supervisor/human_review.py:54-104` (SQLite review tables)
- `arie-backend/production_controls.py:1293-1377` (production control schemas)
- `arie-backend/server.py:1525-1529` (inline ALTER TABLE in staging wipe handler)
- `arie-backend/db.py:_run_migrations` (legacy inline migration pattern; new entries should not be added here — use file-based migrations instead)

## Alternatives considered

- Continue init_db-only and consolidate before prod resume. Rejected: hand-authored consolidation is a class of bug.
- Force all four legacy paths under the policy now. Rejected: out of scope, would block A7.
