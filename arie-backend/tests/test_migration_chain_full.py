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
import sqlite3
import sys
from pathlib import Path

import pytest

# Make arie-backend importable regardless of pytest's cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._migration_idempotency_helpers import fresh_migration_db

_FILE_RUNNER_DATA_MIGRATIONS = {"020", "039"}
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "scripts"


def _migration_files():
    return [
        (path.stem.split("_", 2)[1], path.name)
        for path in sorted(_MIGRATIONS_DIR.glob("migration_*.sql"))
    ]


_CHAIN_FILES = _migration_files()


def _drop_column_if_present(db, table, column):
    if not any(dict(row).get("name") == column for row in db.execute(f"PRAGMA table_info({table})").fetchall()):
        return
    try:
        db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    except sqlite3.OperationalError as exc:
        if "no such column" not in str(exc).lower():
            raise


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
    if "023" not in kept_versions:
        for column in ("client_response_text", "client_response_at", "client_response_by"):
            _drop_column_if_present(db, "application_enhanced_requirements", column)
    if "024" not in kept_versions:
        db.execute("DROP INDEX IF EXISTS idx_documents_current_slot")
        db.execute("DROP INDEX IF EXISTS idx_documents_one_current_slot")
        for column in (
            "slot_key",
            "is_current",
            "version",
            "superseded_at",
            "superseded_by_document_id",
            "replaced_reason",
            "replaced_by_user_id",
        ):
            _drop_column_if_present(db, "documents", column)
    if "025" not in kept_versions:
        db.execute("DROP INDEX IF EXISTS idx_provider_comparisons_app")
        db.execute("DROP INDEX IF EXISTS uq_provider_comparisons_app_pair")
        db.execute("DROP TABLE IF EXISTS screening_provider_comparisons")
    if "032" not in kept_versions:
        db.execute("DROP INDEX IF EXISTS idx_app_enhanced_req_monitoring_alert")
        db.execute("DROP INDEX IF EXISTS idx_app_enhanced_req_monitoring_doc")
        for column in ("monitoring_alert_id", "monitoring_document_id", "due_date"):
            _drop_column_if_present(db, "application_enhanced_requirements", column)
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


def test_monitoring_alert_discovered_via_pg_repair_replaces_all_constraints():
    """Existing PostgreSQL DBs can have old CHECK constraints with varied names."""
    db_source = (Path(__file__).resolve().parents[1] / "db.py").read_text(encoding="utf-8")
    assert "_replace_postgres_column_check_constraint" in db_source
    assert "pg_get_constraintdef(c.oid) ILIKE ?" in db_source
    assert "DROP CONSTRAINT IF EXISTS {_pg_quote_identifier(name)}" in db_source
    assert "DO $$" not in db_source
    assert "DROP CONSTRAINT IF EXISTS %I" not in db_source
    for value in (
        "webhook_live",
        "webhook_backfill",
        "manual_backfill",
        "manual",
        "officer_created",
        "document_health",
    ):
        assert value in db_source


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _FakePostgresDB:
    is_postgres = True

    def __init__(self, constraint_rows):
        self.constraint_rows = constraint_rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "information_schema.tables" in sql:
            return _FakeResult([{"exists": 1}])
        if "information_schema.columns" in sql:
            return _FakeResult([{"exists": 1}])
        if "FROM pg_constraint c" in sql:
            return _FakeResult(self.constraint_rows)
        return _FakeResult([])


def test_pg_check_repair_uses_parameterized_catalog_lookup_and_quotes_identifiers():
    import db as db_module

    fake = _FakePostgresDB(
        [
            {
                "conname": "monitoring_alerts_discovered_via_check",
                "definition": "CHECK ((discovered_via = ANY (...)))",
            },
            {
                "conname": 'legacy weird"check',
                "definition": "CHECK (discovered_via IN ('webhook_live'))",
            },
        ]
    )

    dropped = db_module._replace_postgres_column_check_constraint(
        fake,
        table="monitoring_alerts",
        column="discovered_via",
        constraint_name="monitoring_alerts_discovered_via_check",
        allowed_values=db_module.MONITORING_ALERT_DISCOVERED_VIA_VALUES,
    )

    assert dropped == ["monitoring_alerts_discovered_via_check", 'legacy weird"check']
    catalog_calls = [call for call in fake.calls if "FROM pg_constraint c" in call[0]]
    assert catalog_calls
    assert catalog_calls[0][1] == (
        "monitoring_alerts",
        "%discovered_via%",
        "%discovered_via%",
        "discovered_via",
    )
    assert "%discovered_via%" not in catalog_calls[0][0]
    statements = [sql for sql, _ in fake.calls]
    assert any(
        'DROP CONSTRAINT IF EXISTS "legacy weird""check"' in sql
        for sql in statements
    )
    add_sql = statements[-1]
    assert 'ADD CONSTRAINT "monitoring_alerts_discovered_via_check"' in add_sql
    assert "'manual'" in add_sql
    assert "'officer_created'" in add_sql
    assert "'document_health'" in add_sql


def test_rmi_status_pg_repair_allows_pending_review():
    import db as db_module

    fake = _FakePostgresDB(
        [
            {
                "conname": "rmi_requests_status_check",
                "definition": "CHECK ((status = ANY (ARRAY['open','fulfilled'])))",
            }
        ]
    )

    db_module._replace_postgres_column_check_constraint(
        fake,
        table="rmi_requests",
        column="status",
        constraint_name="rmi_requests_status_check",
        allowed_values=db_module.RMI_REQUEST_STATUS_VALUES,
    )

    add_sql = fake.calls[-1][0]
    for value in ("open", "pending_review", "partially_fulfilled", "fulfilled", "cancelled"):
        assert f"'{value}'" in add_sql


def test_live_pg_constraint_repairs_accept_runtime_values(monkeypatch):
    """Optional PostgreSQL smoke test for long-lived DB CHECK repairs."""
    db_module, db = _fresh_pg(monkeypatch)
    try:
        db.execute(
            "ALTER TABLE monitoring_alerts DROP CONSTRAINT IF EXISTS monitoring_alerts_discovered_via_check"
        )
        db.execute(
            "ALTER TABLE monitoring_alerts ADD CONSTRAINT old_discovered_via_check "
            "CHECK(discovered_via IN ('webhook_live','webhook_backfill','manual_backfill'))"
        )
        db.execute("ALTER TABLE rmi_requests DROP CONSTRAINT IF EXISTS rmi_requests_status_check")
        db.execute(
            "ALTER TABLE rmi_requests ADD CONSTRAINT old_rmi_requests_status_check "
            "CHECK(status IN ('open','partially_fulfilled','fulfilled','cancelled'))"
        )
        db.commit()

        db_module._replace_postgres_column_check_constraint(
            db,
            table="monitoring_alerts",
            column="discovered_via",
            constraint_name="monitoring_alerts_discovered_via_check",
            allowed_values=db_module.MONITORING_ALERT_DISCOVERED_VIA_VALUES,
        )
        db_module._replace_postgres_column_check_constraint(
            db,
            table="rmi_requests",
            column="status",
            constraint_name="rmi_requests_status_check",
            allowed_values=db_module.RMI_REQUEST_STATUS_VALUES,
        )

        for discovered_via in ("manual", "officer_created", "document_health"):
            db.execute(
                "INSERT INTO monitoring_alerts (alert_type, severity, detected_by, summary, discovered_via) "
                "VALUES (?, ?, ?, ?, ?)",
                ("schema_smoke", "medium", "test", "schema smoke", discovered_via),
            )

        db.execute(
            "INSERT INTO clients (id, email, password_hash, company_name) VALUES (?, ?, ?, ?)",
            ("schema_rmi_client", "schema-rmi@example.com", "hash", "Schema RMI Ltd"),
        )
        db.execute(
            """INSERT INTO applications
               (id, ref, client_id, company_name, status)
               VALUES (?, ?, ?, ?, ?)""",
            ("schema_rmi_app", "ARF-SCHEMA-RMI", "schema_rmi_client", "Schema RMI Ltd", "rmi_sent"),
        )
        db.execute(
            """INSERT INTO rmi_requests
               (id, application_id, client_id, status, reason, deadline)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "schema_rmi_request",
                "schema_rmi_app",
                "schema_rmi_client",
                "pending_review",
                "schema smoke",
                "2099-01-01",
            ),
        )
        db.commit()
    finally:
        db.close()
        db_module.close_pg_pool()


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
    """Step 4 case 1: fresh init_db then runner.

    Schema migrations are pre-marked by init_db; data migrations still run.
    """
    with fresh_migration_db(tmp_path, monkeypatch) as db:
        applied, messages = _run_and_capture(db, caplog)
        _assert_no_failed_log(messages)
        assert applied == len(_FILE_RUNNER_DATA_MIGRATIONS), (
            "Expected only file-runner data migrations on fresh DB; "
            f"got {applied}"
        )
        assert any(
            f"Applied {len(_FILE_RUNNER_DATA_MIGRATIONS)} migration(s) successfully" in m
            for m in messages
        ), (
            f"Missing applied-summary log; got: {messages}"
        )
        assert _applied_versions(db) == [v for v, _ in _CHAIN_FILES]


def test_init_db_marks_all_known_migrations_as_applied(monkeypatch):
    """init_db's contract: after running, schema migrations are pre-marked.

    Data migrations are intentionally left pending for the file-based runner.

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

        expected_missing = _FILE_RUNNER_DATA_MIGRATIONS
        assert expected_versions - actual_versions == expected_missing, (
            "init_db marked the wrong migration set as applied. Missing: "
            f"{expected_versions - actual_versions}; expected missing: "
            f"{expected_missing}"
        )

        applied_count = run_all_migrations_with_connection(db)
        assert applied_count == len(_FILE_RUNNER_DATA_MIGRATIONS), (
            "Expected only file-runner data migrations on fresh init_db, "
            f"got {applied_count}"
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
