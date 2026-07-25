"""P12-4 second half / DCI-004 — schema-drift detection (warn-only).

Covers:
- manifest freshness: a FRESH init_db must match the checked-in
  schema_expected.json (the CI gate that keeps the manifest in lockstep with
  schema changes — fails the PR that changes schema without regenerating);
- detection: dropped table / dropped column surface as missing_* findings;
- warn-only contract: check_and_log never raises, even on a broken DB;
- startup wiring: server boot calls the check after migrations, non-fatally;
- PG name parity (skipped without TEST_POSTGRES_DSN): a fresh init_db on real
  PostgreSQL matches the manifest names too.
"""

import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import schema_drift

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def _template_db(tmp_path_factory):
    """A pristine init_db() SQLite file, built ONCE in a subprocess so no
    in-process db-module state (from other suites' reloads) can contaminate
    it. Tests copy it — full isolation per test."""
    path = tmp_path_factory.mktemp("driftref") / "template.db"
    env = dict(os.environ, ENVIRONMENT="testing", DATABASE_URL="",
               DB_PATH=str(path))
    # DB_PATH env is what config/db actually honor for the SQLite path.
    code = "import db; db.init_db(); print('template ok')"
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(BACKEND),
                          env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, (
        f"template init_db failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}")
    return path


class _SqliteWrap:
    """Minimal db-shaped wrapper for introspection tests."""
    is_postgres = False

    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, params=None):
        return self._c.execute(sql, params) if params else self._c.execute(sql)

    def commit(self):
        self._c.commit()

    def close(self):
        self._c.close()


@pytest.fixture
def fresh_sqlite(_template_db, tmp_path):
    """A brand-new copy of the pristine schema — isolated per test."""
    p = tmp_path / "drift.db"
    shutil.copy(_template_db, p)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    wrap = _SqliteWrap(conn)
    yield wrap
    conn.close()


class TestManifestFreshness:
    def test_fresh_init_matches_checked_in_manifest(self, fresh_sqlite):
        live = schema_drift.introspect_schema(fresh_sqlite)
        expected = schema_drift.load_expected()
        findings = schema_drift.compare(expected, live)
        assert not findings["has_any"], (
            "schema_expected.json is stale — regenerate it in this PR:\n"
            "  cd arie-backend && ENVIRONMENT=testing DATABASE_URL= "
            "DB_PATH=/tmp/fresh.db python schema_drift.py --write\n"
            f"findings: {json.dumps(findings, indent=2, sort_keys=True)}"
        )

    def test_manifest_is_nontrivial(self):
        expected = schema_drift.load_expected()
        assert len(expected) >= 60, "manifest suspiciously small"
        assert "applications" in expected and "audit_log" in expected
        assert "risk_config_version" in expected.get("applications", [])


class TestDetection:
    def test_missing_table_and_column_detected(self, fresh_sqlite):
        expected = schema_drift.load_expected()
        fresh_sqlite.execute("ALTER TABLE change_alerts RENAME TO change_alerts_x")
        fresh_sqlite.commit()
        findings = schema_drift.compare(
            expected, schema_drift.introspect_schema(fresh_sqlite))
        assert "change_alerts" in findings["missing_tables"]
        assert "change_alerts_x" in findings["extra_tables"]
        assert findings["has_missing"] is True

    def test_missing_column_detected(self, fresh_sqlite):
        expected = schema_drift.load_expected()
        # SQLite supports DROP COLUMN since 3.35.
        fresh_sqlite.execute("ALTER TABLE clients DROP COLUMN company_name")
        fresh_sqlite.commit()
        findings = schema_drift.compare(
            expected, schema_drift.introspect_schema(fresh_sqlite))
        assert findings["missing_columns"].get("clients") == ["company_name"]
        assert findings["has_missing"] is True

    def test_extra_only_is_not_missing(self, fresh_sqlite):
        expected = schema_drift.load_expected()
        fresh_sqlite.execute("CREATE TABLE zz_new_migration_table (id INTEGER)")
        fresh_sqlite.commit()
        findings = schema_drift.compare(
            expected, schema_drift.introspect_schema(fresh_sqlite))
        assert "zz_new_migration_table" in findings["extra_tables"]
        assert findings["has_missing"] is False


class TestWarnOnlyContract:
    def test_check_and_log_reports_but_never_raises(self, fresh_sqlite, caplog):
        import logging
        fresh_sqlite.execute("ALTER TABLE change_alerts RENAME TO change_alerts_x")
        fresh_sqlite.commit()
        with caplog.at_level(logging.WARNING, logger="arie.schema_drift"):
            findings = schema_drift.check_and_log(fresh_sqlite)
        assert findings["has_missing"] is True
        assert any("SCHEMA DRIFT" in r.getMessage() for r in caplog.records)

    def test_check_and_log_swallows_broken_db(self, caplog):
        import logging

        class _Broken:
            is_postgres = False

            def execute(self, *a, **k):
                raise RuntimeError("db exploded")

        with caplog.at_level(logging.ERROR, logger="arie.schema_drift"):
            findings = schema_drift.check_and_log(_Broken())
        assert findings == {}  # non-fatal, empty result
        assert any("non-fatal" in r.getMessage() for r in caplog.records)

    def test_clean_schema_logs_no_warning(self, fresh_sqlite, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="arie.schema_drift"):
            findings = schema_drift.check_and_log(fresh_sqlite)
        assert findings["has_missing"] is False
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestStartupWiring:
    def test_server_boot_calls_drift_check_nonfatally(self):
        src = (BACKEND / "server.py").read_text(encoding="utf-8")
        idx = src.index("schema drift check")
        block = src[idx - 200: idx + 700]
        assert "check_and_log" in block
        # Wired AFTER migrations, and hard-isolated.
        assert src.index("run_all_migrations()") < idx
        assert "except Exception:" in block and "non-fatal" in block


class TestCliModes:
    def test_check_mode_flags_phantom_table(self, fresh_sqlite, tmp_path, monkeypatch):
        # Point the module at a doctored manifest expecting a phantom table.
        expected = schema_drift.load_expected()
        doctored = dict(expected)
        doctored["phantom_table"] = ["id"]
        p = tmp_path / "manifest.json"
        p.write_text(json.dumps({"tables": doctored}))
        monkeypatch.setattr(schema_drift, "MANIFEST_PATH", p)
        findings = schema_drift.compare(
            schema_drift.load_expected(p), schema_drift.introspect_schema(fresh_sqlite))
        assert "phantom_table" in findings["missing_tables"]


@pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_DSN"),
                    reason="PostgreSQL DSN not available")
class TestPostgresNameParity:
    def test_fresh_pg_init_matches_manifest(self):
        """On real PostgreSQL a fresh init_db must produce the same table and
        column NAMES the manifest records — proving the manifest is
        engine-agnostic and staging's boot-time check compares like-for-like."""
        import psycopg2
        import psycopg2.extras

        dsn = os.environ["TEST_POSTGRES_DSN"]
        base = psycopg2.connect(dsn)
        base.autocommit = True
        dbname = "drift_parity_check"
        with base.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            cur.execute(f'CREATE DATABASE "{dbname}"')
        base.close()

        parity_dsn = dsn.rsplit("/", 1)[0] + f"/{dbname}"
        prev = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = parity_dsn
        try:
            import db as db_module
            importlib.reload(db_module)
            db_module.init_db()
            conn = db_module.get_db()
            try:
                live = schema_drift.introspect_schema(conn)
            finally:
                conn.close()
                db_module.close_pg_pool() if hasattr(db_module, "close_pg_pool") else None
        finally:
            if prev is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev
            importlib.reload(importlib.import_module("db"))

        findings = schema_drift.compare(schema_drift.load_expected(), live)
        assert not findings["missing_tables"], (
            f"PG fresh init missing manifest tables: {findings['missing_tables']}")
        assert not findings["missing_columns"], (
            f"PG fresh init missing manifest columns: {findings['missing_columns']}")
