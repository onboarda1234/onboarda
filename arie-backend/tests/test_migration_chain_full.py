"""
Step 4: full-chain migration validation
========================================
End-to-end assertions that exercise the file-based migration runner
across the three operationally-relevant ``schema_version`` starting
states the deployment surface actually presents:

  1. Fresh database: init_db pre-marks every known migration, so the
     file-based runner applies zero migrations.
  2. Staging-style partial-applied state (001..013 marked applied):
     exactly 2 migrations (014, 015) should apply.
  3. Half-applied state (001..014 marked applied): exactly 1
     migration (015) should apply.

These tests are the contract for PR #128's docker-validate CI job:
the chain must be green from any of the three starting states.

See ``tests/_migration_idempotency_helpers.py`` for the
``fresh_migration_db`` context manager which handles per-test
DB_PATH isolation and config / db module reload teardown.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys

import pytest

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
    ("014", "migration_014_periodic_reviews_status_due_date.sql"),
    ("015", "migration_015_screening_reports_normalized.sql"),
    ("016", "migration_016_screening_reports_normalized_uniqueness.sql"),
    ("017", "migration_017_screening_monitoring_subscriptions.sql"),
    ("018", "migration_018_monitoring_alerts_provider_case_unique.sql"),
    ("019", "migration_019_monitoring_alerts_backfill_provenance.sql"),
    ("020", "migration_020_historical_fixture_backfill.sql"),
]


def _remove_modern_backfills(db, keep_count):
    kept_versions = {v for v, _ in _CHAIN_FILES[:keep_count]}
    if "014" not in kept_versions:
        db.execute("DROP INDEX IF EXISTS idx_periodic_reviews_status")
        for column in ("status", "due_date"):
            try:
                db.execute(f"ALTER TABLE periodic_reviews DROP COLUMN {column}")
            except Exception:
                pass
    if "015" not in kept_versions:
        db.execute("DROP TABLE IF EXISTS screening_reports_normalized")
    if "017" not in kept_versions:
        db.execute("DROP TABLE IF EXISTS screening_monitoring_subscriptions")
    if "018" not in kept_versions:
        db.execute("DROP INDEX IF EXISTS uq_monitoring_alerts_provider_case")
        for column in ("provider", "case_identifier"):
            try:
                db.execute(f"ALTER TABLE monitoring_alerts DROP COLUMN {column}")
            except Exception:
                pass
    if "019" not in kept_versions:
        for column in ("discovered_via", "discovered_at", "backfill_run_id"):
            try:
                db.execute(f"ALTER TABLE monitoring_alerts DROP COLUMN {column}")
            except Exception:
                pass
    db.commit()


def _apply_full_chain_then_rewind(db, keep_count):
    """Rewind schema_version and remove modern backfills for legacy replay."""
    db.execute(
        "DELETE FROM schema_version WHERE version > ?",
        (_CHAIN_FILES[keep_count - 1][0],),
    )
    _remove_modern_backfills(db, keep_count)
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
    _remove_modern_backfills(db, len(prefix))
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


def _dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


def _fresh_pg(monkeypatch):
    dsn = _dsn()
    if not dsn:
        pytest.skip(
            "Set TEST_POSTGRES_DSN or DATABASE_URL_TEST to enable live PG migration tests."
        )
    import psycopg2
    if "db" in sys.modules:
        sys.modules["db"].close_pg_pool()
    with psycopg2.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("ENVIRONMENT", "development")
    import config as config_module
    import db as db_module
    importlib.reload(config_module)
    importlib.reload(db_module)
    db_module.init_db()
    return db_module, db_module.get_db()


def test_fresh_db_applies_all_migrations(
    tmp_path, monkeypatch, caplog
):
    """Step 4 case 1: fresh init_db then runner; migrations are pre-marked."""
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        applied, messages = _run_and_capture(db, caplog)
        _assert_no_failed_log(messages)
        assert applied == 0, (
            f"Expected 0 migrations applied on fresh DB; got {applied}"
        )
        assert any("up to date" in m.lower() for m in messages), (
            f"Missing up-to-date summary log; got: {messages}"
        )
        assert _applied_versions(db) == [v for v, _ in _CHAIN_FILES]


def test_init_db_marks_all_known_migrations_as_applied(monkeypatch):
    """init_db's contract: after running, every migration_*.sql file is
    pre-marked as applied in schema_version. The file-based runner is a
    no-op on a fresh DB.

    This is the regression test for the bug where migration 014's
    ADD COLUMN status would collide with init_db's own status column."""
    db_module, db = _fresh_pg(monkeypatch)
    try:
        from migrations.runner import (
            MIGRATIONS_DIR,
            run_all_migrations_with_connection,
        )

        expected_versions = {
            f.stem.split("_", 2)[1]
            for f in MIGRATIONS_DIR.glob("migration_*.sql")
        }
        actual_versions = {
            r["version"]
            for r in db.execute("SELECT version FROM schema_version").fetchall()
        }

        assert expected_versions <= actual_versions, (
            "init_db did not mark these migrations as applied: "
            f"{expected_versions - actual_versions}"
        )

        applied_count = run_all_migrations_with_connection(db)
        assert applied_count == 0, (
            f"Expected 0 migrations to apply on fresh init_db, got {applied_count}"
        )
    finally:
        db.close()
        db_module.close_pg_pool()


def test_staging_partial_chain_applies_only_remaining(
    tmp_path, monkeypatch, caplog
):
    """Step 4 case 2: simulate a long-lived DB before A7 backfills."""
    expected = len(_CHAIN_FILES) - 13
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        _apply_full_chain_then_rewind(db, keep_count=13)
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
    """Step 4 case 3: simulate a long-lived DB before A4 backfill."""
    expected = len(_CHAIN_FILES) - 14
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        _apply_full_chain_then_rewind(db, keep_count=14)
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
