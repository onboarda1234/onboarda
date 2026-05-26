"""
Migration 004 idempotency tests
================================
Verifies that ``migration_004_documents_s3_key.sql`` is a clean no-op
on a fresh SQLite database.

The original migration ran ``ALTER TABLE documents ADD COLUMN s3_key
TEXT``. On a fresh database the column already exists (added by
``init_db`` and inline migration v2.3 in db.py), so the original SQL
raised ``sqlite3.OperationalError: duplicate column name: s3_key``,
which broke the docker-validate CI job and any first-client SQLite
stand-up.

These tests assert:

  * ``test_fresh_init_db_then_runner_completes_cleanly`` -- the full
    runner completes against a fresh init_db'd SQLite without a FAILED
    log, the "Applied N migration(s) successfully" summary is emitted,
    "004" is recorded in schema_version, and the documents.s3_key
    column is present (via inline v2.3 in db.py, which is the
    authoritative source post-fix).

  * ``test_rerun_when_004_already_applied_is_noop`` -- re-running the
    runner against a database where 004 (and the rest of the chain) is
    already recorded reports zero applied migrations, no FAILED log,
    and emits "Database schema is up to date".
"""

from tests._migration_idempotency_helpers import (
    assert_already_applied_rerun_is_noop,
    assert_fresh_init_then_runner_clean,
    column_exists,
)


VERSION = "004"
FILENAME = "migration_004_documents_s3_key.sql"


def test_fresh_init_db_then_runner_completes_cleanly(
    tmp_path, monkeypatch, caplog
):
    assert_fresh_init_then_runner_clean(
        tmp_path,
        monkeypatch,
        caplog,
        version=VERSION,
        post_condition=lambda db: column_exists(db, "documents", "s3_key"),
    )


def test_rerun_when_004_already_applied_is_noop(
    tmp_path, monkeypatch, caplog
):
    assert_already_applied_rerun_is_noop(
        tmp_path,
        monkeypatch,
        caplog,
        version=VERSION,
        filename=FILENAME,
    )
