# ADR-0008: Schema Migrations Policy

## Status

Accepted, 2026-04-25.

## Date

2026-04-25.

## Context

A2 and A4 added schema changes to `init_db()` only. Production databases that pre-date these changes do not receive them on startup. This creates a prod-resume gap and breaks the audit trail an external reviewer needs.

## Decision

Every schema change must include both an `init_db` update, for fresh installs, and a migration file, for long-lived databases. New migrations go in `arie-backend/migrations/scripts/`; inline `_run_migrations` is deprecated. The CI guard in `lint-and-test` enforces this for changes inside `db.py`'s `_get_postgres_schema()` and `_get_sqlite_schema()` functions.

Migration 015 preserves Phase A4's temporary `CHECK(is_authoritative = 0)` constraint on `screening_reports_normalized`; Phase E activation is the future point at which that constraint may be lifted by a new migration.

## Fresh-install behaviour

On fresh install, `init_db()` builds the complete current schema in one shot. After schema creation, `init_db()` pre-populates the `schema_version` table with every existing `migration_*.sql` file's version, marked with `description="covered by init_db"` and `checksum="init_db"`.

This means the file-based migration runner is a no-op on fresh installs — every known migration is already marked as applied. Migrations are only executed on long-lived databases that pre-date one or more schema changes.

This is the architectural invariant that makes the lockstep policy actually work: when init_db is updated to mirror a migration's schema changes, the migration must not also try to apply those changes on a fresh DB. Pre-populating `schema_version` resolves the conflict.

*Regression test:* `tests/test_migration_chain_full.py::test_init_db_marks_all_known_migrations_as_applied` asserts this invariant.

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
