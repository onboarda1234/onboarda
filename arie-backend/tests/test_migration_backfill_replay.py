from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._migration_idempotency_helpers import FRESH_INIT_PENDING_DATA_MIGRATIONS

COLS = [
    "id", "client_id", "application_id", "provider", "normalized_version",
    "source_screening_report_hash", "normalized_report_json",
    "normalization_status", "normalization_error", "is_authoritative",
    "source", "created_at", "updated_at",
]


def _dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


class TestMigrationBackfillReplay:
    def _fresh_pg(self, monkeypatch):
        dsn = _dsn()
        if not dsn:
            pytest.skip("Set TEST_POSTGRES_DSN or DATABASE_URL_TEST to enable live PG replay tests.")
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

    def _mark_applied(self, db, through):
        from migrations.runner import ensure_schema_version_table
        ensure_schema_version_table(db)
        for i in range(1, through + 1):
            v = str(i).zfill(3)
            db.execute(
                "INSERT INTO schema_version (version, filename, description, checksum) "
                "VALUES (?, ?, '', '') ON CONFLICT(version) DO NOTHING",
                (v, f"migration_{v}_test.sql"),
            )
        db.commit()

    def _run_pending(self, db):
        from migrations.runner import run_all_migrations_with_connection
        return run_all_migrations_with_connection(db)

    def test_migration_014_restores_periodic_reviews_columns(self, monkeypatch):
        db_module, db = self._fresh_pg(monkeypatch)
        try:
            self._mark_applied(db, 13)
            db.execute("DELETE FROM schema_version WHERE version IN ('014','015')")
            db.execute("ALTER TABLE periodic_reviews DROP COLUMN status")
            db.execute("ALTER TABLE periodic_reviews DROP COLUMN due_date")
            db.commit()
            assert self._run_pending(db) == (
                2 + FRESH_INIT_PENDING_DATA_MIGRATIONS
            )
            rows = db.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name='periodic_reviews' AND column_name IN ('status','due_date')"
            ).fetchall()
            assert {r["column_name"]: r["data_type"] for r in rows} == {"status": "text", "due_date": "text"}
            versions = {r["version"] for r in db.execute("SELECT version FROM schema_version").fetchall()}
            assert {"014", "015"} <= versions
        finally:
            db.close()
            db_module.close_pg_pool()

    def test_migration_015_restores_screening_reports_normalized(self, monkeypatch):
        db_module, db = self._fresh_pg(monkeypatch)
        try:
            self._mark_applied(db, 14)
            db.execute("DROP TABLE screening_reports_normalized")
            db.execute("DELETE FROM schema_version WHERE version='015'")
            db.commit()
            assert self._run_pending(db) == (
                1 + FRESH_INIT_PENDING_DATA_MIGRATIONS
            )
            cols = db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='screening_reports_normalized' ORDER BY ordinal_position"
            ).fetchall()
            assert [c["column_name"] for c in cols] == COLS
            idx = {r["indexname"] for r in db.execute("SELECT indexname FROM pg_indexes WHERE tablename='screening_reports_normalized'").fetchall()}
            assert {"idx_screening_normalized_client_app", "idx_screening_normalized_app_id"} <= idx
            checks = db.execute(
                "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conrelid='screening_reports_normalized'::regclass AND contype='c'"
            ).fetchall()
            assert any("is_authoritative = 0" in r["def"] for r in checks)
        finally:
            db.close()
            db_module.close_pg_pool()

    def test_migration_015_check_constraint_blocks_authoritative_writes(self, monkeypatch):
        db_module, db = self._fresh_pg(monkeypatch)
        try:
            self._mark_applied(db, 14)
            db.execute("DROP TABLE screening_reports_normalized")
            db.execute("DELETE FROM schema_version WHERE version='015'")
            db.commit()
            self._run_pending(db)
            import psycopg2
            with psycopg2.connect(_dsn()) as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO screening_reports_normalized (client_id, application_id) VALUES (%s, %s)", ("c1", "a1"))
                    conn.commit()
                    with pytest.raises(psycopg2.IntegrityError):
                        cur.execute("INSERT INTO screening_reports_normalized (client_id, application_id, is_authoritative) VALUES (%s, %s, %s)", ("c2", "a2", 1))
                    conn.rollback()
                    with pytest.raises(psycopg2.IntegrityError):
                        cur.execute("UPDATE screening_reports_normalized SET is_authoritative=1 WHERE client_id=%s", ("c1",))
        finally:
            db.close()
            db_module.close_pg_pool()
