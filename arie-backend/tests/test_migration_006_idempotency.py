"""
Migration 006 idempotency tests
================================
Verifies that ``migration_006_person_dob.sql`` is a clean no-op on a
fresh SQLite database.

The original migration used ``ALTER TABLE ... ADD COLUMN IF NOT
EXISTS date_of_birth TEXT``. SQLite does not support ``ADD COLUMN IF
NOT EXISTS`` (only PostgreSQL does), so on a fresh SQLite database
the file aborted with ``sqlite3.OperationalError: near "EXISTS":
syntax error``, which broke the docker-validate CI job and any
first-client SQLite stand-up.

The ``date_of_birth`` column is now part of the freshly-created
``directors`` and ``ubos`` tables in ``init_db`` for both dialects in
db.py, so the file can be a documented no-op.

These tests assert:

  * ``test_fresh_init_db_then_runner_completes_cleanly`` -- the full
    runner completes against a fresh init_db'd SQLite without a FAILED
    log, the "Applied N migration(s) successfully" summary is emitted,
    "006" is recorded in schema_version, and the directors.date_of_birth
    and ubos.date_of_birth columns are present (sourced from init_db
    post-fix).

  * ``test_rerun_when_006_already_applied_is_noop`` -- re-running the
    runner against a database where 006 (and the rest of the chain) is
    already recorded reports zero applied migrations, no FAILED log,
    and emits "Database schema is up to date".
"""

from tests._migration_idempotency_helpers import (
    assert_already_applied_rerun_is_noop,
    assert_fresh_init_then_runner_clean,
    column_exists,
)


VERSION = "006"
FILENAME = "migration_006_person_dob.sql"


def _post_condition(db):
    return (
        column_exists(db, "directors", "date_of_birth")
        and column_exists(db, "ubos", "date_of_birth")
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


def test_rerun_when_006_already_applied_is_noop(
    tmp_path, monkeypatch, caplog
):
    assert_already_applied_rerun_is_noop(
        tmp_path,
        monkeypatch,
        caplog,
        version=VERSION,
        filename=FILENAME,
    )
