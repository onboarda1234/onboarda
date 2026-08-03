"""Migration 056: source-status binding for Monitoring review requests."""

from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_module


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND
    / "migrations"
    / "scripts"
    / "migration_056_monitoring_review_source_status.sql"
)


def _review_request_table_block(schema: str) -> str:
    marker = "CREATE TABLE IF NOT EXISTS monitoring_alert_review_requests ("
    start = schema.index(marker)
    return schema[start:schema.index("\n    );", start)]


def _legacy_sqlite_wrapper():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """
        CREATE TABLE monitoring_alert_review_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL,
            evidence_ref TEXT,
            transition_evidence TEXT,
            state TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE UNIQUE INDEX uq_monitoring_review_requests_one_pending
            ON monitoring_alert_review_requests(alert_id)
            WHERE state = 'pending';
        INSERT INTO monitoring_alert_review_requests
            (alert_id, evidence_ref, transition_evidence, state)
        VALUES
            (41, 'existing-reference', '{"document_id":"doc-41"}', 'pending');
        """
    )
    raw.commit()
    return db_module.DBConnection(raw, is_postgres=False)


def test_fresh_schemas_have_nullable_source_status_binding():
    for schema in (
        db_module._get_postgres_schema(),
        db_module._get_sqlite_schema(),
    ):
        block = _review_request_table_block(schema)
        assert (
            "transition_evidence TEXT,\n"
            "        source_alert_status TEXT,\n"
            "        state TEXT"
        ) in block
        assert "source_alert_status TEXT NOT NULL" not in block
        assert "source_alert_status TEXT DEFAULT" not in block


def test_existing_sqlite_upgrade_preserves_rows_index_and_is_idempotent():
    db = _legacy_sqlite_wrapper()
    try:
        before = db.execute(
            "SELECT id, alert_id, evidence_ref, transition_evidence, state "
            "FROM monitoring_alert_review_requests"
        ).fetchone()
        index_before = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (db_module.MONITORING_PENDING_REVIEW_UNIQUE_INDEX,),
        ).fetchone()["sql"]

        first = db_module._ensure_monitoring_review_source_status_schema(db)
        after = db.execute(
            "SELECT id, alert_id, evidence_ref, transition_evidence, state, "
            "source_alert_status FROM monitoring_alert_review_requests"
        ).fetchone()
        second = db_module._ensure_monitoring_review_source_status_schema(db)
        index_after = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (db_module.MONITORING_PENDING_REVIEW_UNIQUE_INDEX,),
        ).fetchone()["sql"]

        assert first == {
            "engine": "sqlite",
            "changed": True,
            "nullable": True,
            "default": None,
        }
        assert second["changed"] is False
        assert {
            key: after[key]
            for key in (
                "id",
                "alert_id",
                "evidence_ref",
                "transition_evidence",
                "state",
            )
        } == dict(before)
        assert after["source_alert_status"] is None
        assert index_after == index_before
    finally:
        db.close()


def test_migration_file_is_additive_idempotent_and_does_not_touch_index():
    db = _legacy_sqlite_wrapper()
    try:
        index_before = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (db_module.MONITORING_PENDING_REVIEW_UNIQUE_INDEX,),
        ).fetchone()["sql"]
        sql = MIGRATION.read_text(encoding="utf-8")
        db.executescript(sql)
        db.executescript(sql)
        row = db.execute(
            "SELECT evidence_ref, transition_evidence, state, "
            "source_alert_status FROM monitoring_alert_review_requests"
        ).fetchone()
        index_after = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (db_module.MONITORING_PENDING_REVIEW_UNIQUE_INDEX,),
        ).fetchone()["sql"]
        assert row == {
            "evidence_ref": "existing-reference",
            "transition_evidence": '{"document_id":"doc-41"}',
            "state": "pending",
            "source_alert_status": None,
        }
        assert index_after == index_before
    finally:
        db.close()

    executable = "\n".join(
        line
        for line in MIGRATION.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )
    normalized = " ".join(executable.split()).upper()
    assert normalized == (
        "ALTER TABLE MONITORING_ALERT_REVIEW_REQUESTS "
        "ADD COLUMN IF NOT EXISTS SOURCE_ALERT_STATUS TEXT;"
    )
    assert "UPDATE " not in normalized
    assert "DELETE " not in normalized
    assert "INSERT " not in normalized
    assert "CREATE INDEX" not in normalized
    assert "DROP COLUMN IF EXISTS source_alert_status" in MIGRATION.read_text(
        encoding="utf-8"
    )


def test_startup_installs_binding_before_marking_migration_covered():
    source = (BACKEND / "db.py").read_text(encoding="utf-8")
    init_body = source[
        source.index("def init_db():"):
        source.index("def _mark_known_migrations_as_applied")
    ]
    evidence_install = init_body.index(
        "_ensure_monitoring_review_transition_evidence_schema(db)"
    )
    binding_install = init_body.index(
        "_ensure_monitoring_review_source_status_schema(db)"
    )
    ledger = init_body.index("_mark_known_migrations_as_applied(db)")
    assert evidence_install < binding_install < ledger
    assert "056" not in db_module.FILE_MIGRATIONS_REQUIRING_RUNNER


def test_schema_manifest_tracks_source_status_binding():
    import schema_drift

    expected = schema_drift.load_expected()
    assert (
        "source_alert_status"
        in expected["monitoring_alert_review_requests"]
    )


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _FakePostgres:
    is_postgres = True

    def __init__(self):
        self.column = None
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        normalized = " ".join(sql.split())
        if "FROM information_schema.tables" in normalized:
            return _Result([{"exists": 1}])
        if "FROM information_schema.columns" in normalized:
            return _Result([self.column] if self.column else [])
        if normalized.startswith(
            "ALTER TABLE monitoring_alert_review_requests ADD COLUMN"
        ):
            self.column = {
                "data_type": "text",
                "is_nullable": "YES",
                "column_default": None,
            }
            return _Result([])
        raise AssertionError(f"unexpected SQL: {normalized}")


def test_postgres_upgrade_is_native_additive_and_idempotent():
    fake = _FakePostgres()
    first = db_module._ensure_monitoring_review_source_status_schema(fake)
    second = db_module._ensure_monitoring_review_source_status_schema(fake)
    alters = [
        " ".join(sql.split())
        for sql, _params in fake.calls
        if sql.lstrip().upper().startswith("ALTER TABLE")
    ]
    assert first["engine"] == "postgresql"
    assert first["changed"] is True
    assert second["changed"] is False
    assert alters == [
        "ALTER TABLE monitoring_alert_review_requests "
        "ADD COLUMN source_alert_status TEXT"
    ]


def _postgres_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get(
        "DATABASE_URL_TEST"
    )


@pytest.mark.skipif(not _postgres_dsn(), reason="No disposable PostgreSQL test DSN")
def test_live_postgres_upgrade_preserves_existing_row_and_partial_index():
    dsn = _postgres_dsn()
    parts = urlsplit(dsn)
    if parts.hostname not in (None, "localhost", "127.0.0.1", "::1"):
        pytest.skip("PostgreSQL migration test requires a local disposable server")
    if "test" not in (parts.path or "").lower():
        pytest.skip("PostgreSQL migration test requires a test-named database")

    import psycopg2

    schema = f"monitoring_review_source_status_{uuid.uuid4().hex[:12]}"
    raw = psycopg2.connect(dsn)
    db = db_module.DBConnection(raw, is_postgres=True, database_identity=dsn)
    try:
        db.execute(f'CREATE SCHEMA "{schema}"')
        db.execute(f'SET search_path TO "{schema}"')
        db.execute(
            "CREATE TABLE monitoring_alert_review_requests ("
            "id SERIAL PRIMARY KEY, alert_id INTEGER NOT NULL, "
            "transition_evidence TEXT, state TEXT NOT NULL DEFAULT 'pending')"
        )
        db.execute(
            "CREATE UNIQUE INDEX "
            f"{db_module.MONITORING_PENDING_REVIEW_UNIQUE_INDEX} "
            "ON monitoring_alert_review_requests(alert_id) "
            "WHERE state = 'pending'"
        )
        db.execute(
            "INSERT INTO monitoring_alert_review_requests "
            "(alert_id, transition_evidence) VALUES (?, ?)",
            (77, '{"document_id":"doc-77"}'),
        )
        db.commit()

        index_before = db_module._monitoring_pending_review_index_definition(db)
        first = db_module._ensure_monitoring_review_source_status_schema(db)
        db.commit()
        second = db_module._ensure_monitoring_review_source_status_schema(db)
        row = db.execute(
            "SELECT alert_id, transition_evidence, state, source_alert_status "
            "FROM monitoring_alert_review_requests"
        ).fetchone()
        index_after = db_module._monitoring_pending_review_index_definition(db)

        assert first["changed"] is True
        assert second["changed"] is False
        assert row == {
            "alert_id": 77,
            "transition_evidence": '{"document_id":"doc-77"}',
            "state": "pending",
            "source_alert_status": None,
        }
        assert index_after == index_before
    finally:
        try:
            db.rollback()
            db.execute("SET search_path TO public")
            db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            db.commit()
        finally:
            db.close()
