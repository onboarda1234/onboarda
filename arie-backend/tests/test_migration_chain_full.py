"""
Step 4: full-chain migration validation
========================================
End-to-end assertions that exercise the file-based migration runner
across the three operationally-relevant ``schema_version`` starting
states the deployment surface actually presents:

  1. Fresh database (no schema_version rows): exactly 12 migrations
     should apply, in order, with no FAILED log line.
  2. Staging-style partial-applied state (001..009 marked applied):
     exactly 3 migrations (010, 011, 012) should apply.
  3. Half-applied state (001..005 marked applied): exactly 7
     migrations (006..012) should apply.

These tests are the contract for PR #128's docker-validate CI job:
the chain must be green from any of the three starting states.

See ``tests/_migration_idempotency_helpers.py`` for the
``fresh_migration_db`` context manager which handles per-test
DB_PATH isolation and config / db module reload teardown.
"""

from __future__ import annotations

import logging
import os
import sys

# Make arie-backend importable regardless of pytest's cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._migration_idempotency_helpers import fresh_migration_db


_CHAIN_FILES = [
    ("001", "migration_001_initial.sql"),
    ("002", "migration_002_supervisor_tables.sql"),
    ("003", "migration_003_monitoring_indexes.sql"),
    ("004", "migration_004_documents_s3_key.sql"),
    ("005", "migration_005_applications_truth_schema.sql"),
    ("006", "migration_006_person_dob.sql"),
    ("007", "migration_007_screening_reports_normalized.sql"),
    ("008", "migration_008_lifecycle_linkage.sql"),
    ("009", "migration_009_periodic_review_operating_model.sql"),
    ("010", "migration_010_edd_memo_integration.sql"),
    ("011", "migration_011_edd_memo_attachment_uniqueness.sql"),
    ("012", "migration_012_legacy_unmapped_audit_classification.sql"),
    ("013", "migration_013_periodic_review_memos.sql"),
]


def _apply_full_chain_then_rewind(db, keep_count):
    """Apply the full migration chain, then DELETE the trailing
    ``schema_version`` rows so only the first ``keep_count`` versions
    are recorded. The actual schema is in the post-012 state, so the
    runner will (re-)apply migrations N+1..12. **Only safe** when the
    re-applied migrations are idempotent against the post-012 schema
    (CREATE TABLE / INDEX IF NOT EXISTS, INSERT WHERE NOT EXISTS).
    Migrations 010 / 011 / 012 satisfy this; 008 / 009 do not (they
    use plain ALTER TABLE ADD COLUMN). Use ``_preseed_schema_version``
    instead when re-applying 008 or 009."""
    from migrations.runner import run_all_migrations_with_connection
    run_all_migrations_with_connection(db)
    db.execute(
        "DELETE FROM schema_version WHERE version > ?",
        (_CHAIN_FILES[keep_count - 1][0],),
    )
    db.commit()


def _preseed_schema_version(db, prefix):
    """Mark every migration in ``prefix`` as already applied without
    actually running it. Safe for migrations 001..007 because:

      * 001..003 set up tables that ``init_db`` already creates;
      * 004..007 are documented no-ops post-fix.

    The runner subsequently applies the rest of the chain against a
    schema that does not yet have 008+'s additions, which lets
    plain-ALTER-TABLE migrations like 008 / 009 succeed."""
    from migrations.runner import ensure_schema_version_table
    ensure_schema_version_table(db)
    for version, filename in prefix:
        db.execute(
            "INSERT OR IGNORE INTO schema_version "
            "(version, filename, description, checksum) "
            "VALUES (?, ?, ?, ?)",
            (version, filename, "preseeded by test", "preseed"),
        )
    db.commit()


def _run_and_capture(db, caplog):
    """Run the runner and return (applied_count, list_of_log_messages)."""
    caplog.clear()
    from migrations.runner import run_all_migrations_with_connection
    with caplog.at_level(logging.INFO, logger="arie.migrations"):
        applied = run_all_migrations_with_connection(db)
    messages = [
        r.getMessage() for r in caplog.records
        if r.name == "arie.migrations"
    ]
    return applied, messages


def _assert_no_failed_log(messages):
    assert not any("failed" in m.lower() for m in messages), (
        f"Migration runner emitted a FAILED log: {messages}"
    )


def _applied_versions(db):
    rows = db.execute(
        "SELECT version FROM schema_version ORDER BY version"
    ).fetchall()
    return [r["version"] for r in rows]


def test_fresh_db_applies_all_migrations(
    tmp_path, monkeypatch, caplog
):
    """Step 4 case 1: fresh init_db then runner; all chained migrations apply."""
    expected = len(_CHAIN_FILES)
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        applied, messages = _run_and_capture(db, caplog)
        _assert_no_failed_log(messages)
        assert applied == expected, (
            f"Expected {expected} migrations applied on fresh DB; got {applied}"
        )
        assert any(
            m.startswith(f"Applied {expected} migration(s) successfully")
            for m in messages
        ), f"Missing summary log; got: {messages}"
        assert _applied_versions(db) == [v for v, _ in _CHAIN_FILES]


def test_staging_partial_chain_applies_only_remaining(
    tmp_path, monkeypatch, caplog
):
    """Step 4 case 2: simulate staging where 001..009 are already
    recorded; runner should apply only 010..latest. Migrations 010..
    latest are idempotent against an existing post-009 schema (CREATE
    ... IF NOT EXISTS, INSERT ... WHERE NOT EXISTS), so the
    re-application succeeds."""
    expected = len(_CHAIN_FILES) - 9
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        _apply_full_chain_then_rewind(db, keep_count=9)  # keep 001..009
        applied, messages = _run_and_capture(db, caplog)
        _assert_no_failed_log(messages)
        assert applied == expected, (
            f"Expected exactly {expected} migrations on staging; "
            f"got {applied}; messages={messages}"
        )
        assert any(
            m.startswith(f"Applied {expected} migration(s) successfully")
            for m in messages
        ), f"Missing summary log; got: {messages}"
        assert _applied_versions(db) == [v for v, _ in _CHAIN_FILES]


def test_half_applied_chain_applies_remaining(
    tmp_path, monkeypatch, caplog
):
    """Step 4 case 3: half-applied state where 001..005 are already
    recorded; runner should apply 006..latest.

    Strategy: ``init_db`` already provides the schema 001..005 would
    establish (those migrations are no-ops or duplicate init_db
    tables post-fix), so it is safe to mark them applied without
    running. The runner then applies 006..latest against a schema that
    does not yet have 008's added columns, which keeps 008's plain
    ALTER TABLE statements valid."""
    expected = len(_CHAIN_FILES) - 5
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        _preseed_schema_version(db, _CHAIN_FILES[:5])  # 001..005
        applied, messages = _run_and_capture(db, caplog)
        _assert_no_failed_log(messages)
        assert applied == expected, (
            f"Expected exactly {expected} migrations (006..latest) on "
            f"half-applied state; got {applied}; messages={messages}"
        )
        assert any(
            m.startswith(f"Applied {expected} migration(s) successfully")
            for m in messages
        ), f"Missing summary log; got: {messages}"
        assert _applied_versions(db) == [v for v, _ in _CHAIN_FILES]
