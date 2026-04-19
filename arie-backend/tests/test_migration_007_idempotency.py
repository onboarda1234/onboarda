"""
Migration 007 idempotency tests
================================
Verifies that ``migration_007_screening_reports_normalized.sql`` is a
clean no-op on a fresh SQLite database.

The original migration used PostgreSQL-only DDL constructs (``id
SERIAL PRIMARY KEY`` and ``to_char(now() AT TIME ZONE 'UTC', ...)``
defaults), which the original file header itself documented as a
"controlled exception" pending a dialect-aware migration runner. On a
fresh SQLite database the file aborted with
``sqlite3.OperationalError: near "AT": syntax error``, breaking the
docker-validate CI job and any first-client SQLite stand-up.

Because ``ENABLE_SCREENING_ABSTRACTION`` defaults to ``false`` and no
EX-validated control reads the table, the file is now a documented
no-op so the migration chain applies cleanly on every dialect.
Re-introduction of the ``screening_reports_normalized`` table on
fresh PostgreSQL stand-ups is deferred to a future dialect-aware
migration once the runner gains per-dialect SQL support (this is
unchanged from the prior known-gap status of this file).

These tests assert:

  * ``test_fresh_init_db_then_runner_completes_cleanly`` -- the full
    runner completes against a fresh init_db'd SQLite without a FAILED
    log, the "Applied N migration(s) successfully" summary is emitted,
    and "007" is recorded in schema_version.

    There is no schema post-condition because the
    ``screening_reports_normalized`` table is intentionally not created
    on SQLite (the table is PG-only and has no SQLite consumers).

  * ``test_rerun_when_007_already_applied_is_noop`` -- re-running the
    runner against a database where 007 (and the rest of the chain) is
    already recorded reports zero applied migrations, no FAILED log,
    and emits "Database schema is up to date".
"""

from tests._migration_idempotency_helpers import (
    assert_already_applied_rerun_is_noop,
    assert_fresh_init_then_runner_clean,
)


VERSION = "007"
FILENAME = "migration_007_screening_reports_normalized.sql"


def test_fresh_init_db_then_runner_completes_cleanly(
    tmp_path, monkeypatch, caplog
):
    assert_fresh_init_then_runner_clean(
        tmp_path,
        monkeypatch,
        caplog,
        version=VERSION,
        post_condition=None,
    )


def test_rerun_when_007_already_applied_is_noop(
    tmp_path, monkeypatch, caplog
):
    assert_already_applied_rerun_is_noop(
        tmp_path,
        monkeypatch,
        caplog,
        version=VERSION,
        filename=FILENAME,
    )
