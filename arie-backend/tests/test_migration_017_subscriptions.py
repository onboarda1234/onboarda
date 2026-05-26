"""Parity tests for migration 017 — screening_monitoring_subscriptions."""

from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._migration_idempotency_helpers import FRESH_INIT_PENDING_DATA_MIGRATIONS

COLS_017 = [
    "id", "client_id", "application_id", "provider", "person_key",
    "customer_identifier", "external_subscription_id", "status",
    "subscribed_at", "last_event_at", "last_webhook_type",
    "monitoring_event_count", "is_authoritative", "source",
    "created_at", "updated_at",
]

INDEXES_017 = {
    "idx_screening_monitoring_subs_app",
    "idx_screening_monitoring_subs_client",
    "uq_screening_monitoring_subs_customer",
}


def _dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


def _fresh_pg(monkeypatch):
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


def _run_017_from_migration(db):
    from migrations.runner import run_all_migrations_with_connection
    db.execute("DROP TABLE IF EXISTS screening_monitoring_subscriptions")
    db.execute("DELETE FROM schema_version WHERE version='017'")
    db.commit()
    assert run_all_migrations_with_connection(db) == (
        1 + FRESH_INIT_PENDING_DATA_MIGRATIONS
    )


def test_migration_017_creates_subscriptions_table_postgres(monkeypatch):
    db_module, db = _fresh_pg(monkeypatch)
    try:
        _run_017_from_migration(db)
        row = db.execute("SELECT to_regclass('public.screening_monitoring_subscriptions')").fetchone()
        assert list(row.values())[0] is not None
        cols = db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='screening_monitoring_subscriptions' ORDER BY ordinal_position"
        ).fetchall()
        assert [c["column_name"] for c in cols] == COLS_017
        idx = {
            r["indexname"]
            for r in db.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='screening_monitoring_subscriptions'"
            ).fetchall()
        }
        assert INDEXES_017 <= idx
        checks = db.execute(
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid='screening_monitoring_subscriptions'::regclass AND contype='c'"
        ).fetchall()
        assert len(checks) >= 2
    finally:
        db.close()
        db_module.close_pg_pool()


def test_migration_017_check_constraint_blocks_authoritative_writes(monkeypatch):
    db_module, db = _fresh_pg(monkeypatch)
    try:
        _run_017_from_migration(db)
        import psycopg2
        with psycopg2.connect(_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO screening_monitoring_subscriptions "
                    "(client_id, application_id, provider, customer_identifier) "
                    "VALUES (%s, %s, %s, %s)",
                    ("c1", "a1", "complyadvantage", "cust-1"),
                )
                conn.commit()
                with pytest.raises(psycopg2.IntegrityError):
                    cur.execute(
                        "INSERT INTO screening_monitoring_subscriptions "
                        "(client_id, application_id, provider, customer_identifier, is_authoritative) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        ("c1", "a2", "complyadvantage", "cust-2", 1),
                    )
                conn.rollback()
                with pytest.raises(psycopg2.IntegrityError):
                    cur.execute(
                        "UPDATE screening_monitoring_subscriptions SET is_authoritative=1 "
                        "WHERE customer_identifier=%s",
                        ("cust-1",),
                    )
    finally:
        db.close()
        db_module.close_pg_pool()


def test_migration_017_check_constraint_blocks_invalid_status(monkeypatch):
    db_module, db = _fresh_pg(monkeypatch)
    try:
        _run_017_from_migration(db)
        import psycopg2
        with psycopg2.connect(_dsn()) as conn:
            with conn.cursor() as cur:
                with pytest.raises(psycopg2.IntegrityError):
                    cur.execute(
                        "INSERT INTO screening_monitoring_subscriptions "
                        "(client_id, application_id, provider, customer_identifier, status) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        ("c1", "a1", "complyadvantage", "bad-status", "garbage"),
                    )
                conn.rollback()
                for status in ("active", "paused", "cancelled", "expired"):
                    cur.execute(
                        "INSERT INTO screening_monitoring_subscriptions "
                        "(client_id, application_id, provider, customer_identifier, status) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        ("c1", f"app-{status}", "complyadvantage", f"cust-{status}", status),
                    )
                conn.commit()
    finally:
        db.close()
        db_module.close_pg_pool()


def test_migration_017_unique_constraint_includes_client_id(monkeypatch):
    db_module, db = _fresh_pg(monkeypatch)
    try:
        _run_017_from_migration(db)
        import psycopg2
        with psycopg2.connect(_dsn()) as conn:
            with conn.cursor() as cur:
                for client_id, app_id in (("c1", "a1"), ("c2", "a2")):
                    cur.execute(
                        "INSERT INTO screening_monitoring_subscriptions "
                        "(client_id, application_id, provider, customer_identifier) "
                        "VALUES (%s, %s, %s, %s)",
                        (client_id, app_id, "complyadvantage", "shared-customer"),
                    )
                conn.commit()
                with pytest.raises(psycopg2.IntegrityError):
                    cur.execute(
                        "INSERT INTO screening_monitoring_subscriptions "
                        "(client_id, application_id, provider, customer_identifier) "
                        "VALUES (%s, %s, %s, %s)",
                        ("c1", "a3", "complyadvantage", "shared-customer"),
                    )
    finally:
        db.close()
        db_module.close_pg_pool()


def test_migration_017_columns_in_canonical_order(monkeypatch):
    db_module, db = _fresh_pg(monkeypatch)
    try:
        _run_017_from_migration(db)
        cols = db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='screening_monitoring_subscriptions' ORDER BY ordinal_position"
        ).fetchall()
        assert [c["column_name"] for c in cols] == COLS_017
    finally:
        db.close()
        db_module.close_pg_pool()
