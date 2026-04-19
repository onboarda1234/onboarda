"""
Migration 005 idempotency tests
================================
Verifies that ``migration_005_applications_truth_schema.sql`` is a
clean no-op on a fresh SQLite database.

The original migration used ``ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`` for the directors / ubos / documents column additions. SQLite
does not support ``ADD COLUMN IF NOT EXISTS`` (only PostgreSQL does),
so on a fresh SQLite database the file aborted with
``sqlite3.OperationalError: near "EXISTS": syntax error``, which broke
the docker-validate CI job and any first-client SQLite stand-up.

All the columns and the ``intermediaries`` table are now created by
``init_db`` and inline migrations v2.5 / v2.7 in db.py, so the file
can be a documented no-op.

These tests assert:

  * ``test_fresh_init_db_then_runner_completes_cleanly`` -- the full
    runner completes against a fresh init_db'd SQLite without a FAILED
    log, the "Applied N migration(s) successfully" summary is emitted,
    "005" is recorded in schema_version, and a representative subset
    of the columns / tables the original migration was responsible
    for are present (sourced from init_db / inline v2.5 / inline v2.7
    post-fix).

  * ``test_rerun_when_005_already_applied_is_noop`` -- re-running the
    runner against a database where 005 (and the rest of the chain) is
    already recorded reports zero applied migrations, no FAILED log,
    and emits "Database schema is up to date".
"""

from tests._migration_idempotency_helpers import (
    assert_already_applied_rerun_is_noop,
    assert_fresh_init_then_runner_clean,
    column_exists,
    table_exists,
)


VERSION = "005"
FILENAME = "migration_005_applications_truth_schema.sql"


def _post_condition(db):
    return (
        column_exists(db, "directors", "person_key")
        and column_exists(db, "directors", "first_name")
        and column_exists(db, "directors", "last_name")
        and column_exists(db, "directors", "pep_declaration")
        and column_exists(db, "ubos", "person_key")
        and column_exists(db, "ubos", "pep_declaration")
        and column_exists(db, "documents", "review_status")
        and column_exists(db, "documents", "review_comment")
        and column_exists(db, "documents", "reviewed_by")
        and column_exists(db, "documents", "reviewed_at")
        and table_exists(db, "intermediaries")
    )


def test_fresh_init_db_then_runner_completes_cleanly(
    tmp_path, monkeypatch, caplog
):
    assert_fresh_init_then_runner_clean(
        tmp_path,
        monkeypatch,
        caplog,
        version=VERSION,
        post_condition=_post_condition,
    )


def test_rerun_when_005_already_applied_is_noop(
    tmp_path, monkeypatch, caplog
):
    assert_already_applied_rerun_is_noop(
        tmp_path,
        monkeypatch,
        caplog,
        version=VERSION,
        filename=FILENAME,
    )
