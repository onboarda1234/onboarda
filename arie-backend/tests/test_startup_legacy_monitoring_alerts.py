"""Startup regression tests for legacy monitoring_alerts schemas.

These tests run db.py reload/startup work in subprocesses so temporary
DATABASE_URL/DB_PATH changes cannot leak into the parent pytest process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_startup_script(script: str, env: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"startup regression subprocess failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_sqlite_init_db_repairs_legacy_monitoring_alerts_identity(tmp_path):
    """A legacy SQLite table missing CA identity columns is repaired at startup."""
    env = os.environ.copy()
    env["DATABASE_URL"] = ""
    env["ENVIRONMENT"] = "development"
    env["DB_PATH"] = str(tmp_path / "legacy_monitoring.db")

    _run_startup_script(
        """
        import re

        import db as db_module

        def legacy_schema_without_monitoring_identity(schema_sql):
            assert "uq_monitoring_alerts_provider_case" not in schema_sql
            legacy_sql, replacements = re.subn(
                r"(\\n\\s*application_id TEXT REFERENCES applications\\(id\\) ON DELETE CASCADE,\\n)"
                r"\\s*provider TEXT,\\n\\s*case_identifier TEXT,\\n",
                r"\\1",
                schema_sql,
                count=1,
            )
            assert replacements == 1
            return legacy_sql

        legacy_db = db_module.get_db()
        legacy_db.executescript(
            legacy_schema_without_monitoring_identity(db_module._get_sqlite_schema())
        )
        legacy_db.commit()
        pre_cols = {
            r["name"]
            for r in legacy_db.execute("PRAGMA table_info(monitoring_alerts)").fetchall()
        }
        assert "provider" not in pre_cols
        assert "case_identifier" not in pre_cols
        legacy_db.close()

        db_module.init_db()

        db = db_module.get_db()
        cols = {r["name"] for r in db.execute("PRAGMA table_info(monitoring_alerts)").fetchall()}
        indexes = {r["name"] for r in db.execute("PRAGMA index_list(monitoring_alerts)").fetchall()}
        db.close()
        assert {"provider", "case_identifier"} <= cols
        assert "uq_monitoring_alerts_provider_case" in indexes
        """,
        env,
    )


def test_pg_init_db_repairs_legacy_monitoring_alerts_identity():
    """A legacy PostgreSQL table missing CA identity columns is repaired at startup."""
    dsn = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")
    if not dsn:
        pytest.skip("Set TEST_POSTGRES_DSN or DATABASE_URL_TEST to enable PG startup regression test.")

    env = os.environ.copy()
    env["DATABASE_URL"] = dsn
    env["ENVIRONMENT"] = "development"

    _run_startup_script(
        """
        import re

        import psycopg2
        import db as db_module

        def legacy_schema_without_monitoring_identity(schema_sql):
            assert "uq_monitoring_alerts_provider_case" not in schema_sql
            legacy_sql, replacements = re.subn(
                r"(\\n\\s*application_id TEXT REFERENCES applications\\(id\\) ON DELETE CASCADE,\\n)"
                r"\\s*provider TEXT,\\n\\s*case_identifier TEXT,\\n",
                r"\\1",
                schema_sql,
                count=1,
            )
            assert replacements == 1
            return legacy_sql

        dsn = __import__("os").environ["DATABASE_URL"]
        with psycopg2.connect(dsn) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA public CASCADE")
                cur.execute("CREATE SCHEMA public")

        legacy_db = db_module.get_db()
        legacy_db.executescript(
            legacy_schema_without_monitoring_identity(db_module._get_postgres_schema())
        )
        legacy_db.commit()
        pre_cols = {
            r["column_name"]
            for r in legacy_db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'monitoring_alerts'"
            ).fetchall()
        }
        assert "provider" not in pre_cols
        assert "case_identifier" not in pre_cols
        legacy_db.close()

        db_module.init_db()

        db = db_module.get_db()
        cols = {
            r["column_name"]
            for r in db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'monitoring_alerts'"
            ).fetchall()
        }
        indexes = {
            r["indexname"]
            for r in db.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'monitoring_alerts'"
            ).fetchall()
        }
        db.close()
        db_module.close_pg_pool()
        assert {"provider", "case_identifier"} <= cols
        assert "uq_monitoring_alerts_provider_case" in indexes
        """,
        env,
    )
