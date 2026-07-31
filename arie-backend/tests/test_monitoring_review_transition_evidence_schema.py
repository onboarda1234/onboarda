"""Migration 055: typed evidence persistence for Monitoring review controls."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_module
import monitoring_dismissal_control as dismissal_control


BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND
    / "migrations"
    / "scripts"
    / "migration_055_monitoring_review_transition_evidence.sql"
)


def _review_request_table_block(schema: str) -> str:
    marker = "CREATE TABLE IF NOT EXISTS monitoring_alert_review_requests ("
    start = schema.index(marker)
    return schema[start:schema.index("\n    );", start)]


def test_fresh_postgres_and_sqlite_schemas_have_nullable_text_column():
    for schema in (
        db_module._get_postgres_schema(),
        db_module._get_sqlite_schema(),
    ):
        block = _review_request_table_block(schema)
        assert (
            "evidence_ref TEXT,\n"
            "        transition_evidence TEXT,\n"
            "        source_alert_status TEXT,\n"
            "        state TEXT"
        ) in block
        assert "transition_evidence TEXT NOT NULL" not in block
        assert "transition_evidence TEXT DEFAULT" not in block
        assert "source_alert_status TEXT NOT NULL" not in block
        assert "source_alert_status TEXT DEFAULT" not in block


def test_pending_review_index_name_matches_runtime_conflict_handler():
    assert (
        db_module.MONITORING_PENDING_REVIEW_UNIQUE_INDEX
        == dismissal_control.PENDING_REVIEW_UNIQUE_INDEX
    )


def test_fresh_sqlite_persists_supported_typed_evidence_json(temp_db):
    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    try:
        metadata = {
            row["name"]: row
            for row in conn.execute(
                "PRAGMA table_info(monitoring_alert_review_requests)"
            ).fetchall()
        }["transition_evidence"]
        assert metadata["type"].upper() == "TEXT"
        assert metadata["notnull"] == 0
        assert metadata["dflt_value"] is None

        alert_id = conn.execute(
            "INSERT INTO monitoring_alerts (status, summary) VALUES (?, ?)",
            ("open", "typed evidence persistence probe"),
        ).lastrowid
        payload = json.dumps(
            {
                "review_request_id": "review-17",
                "screening_case_id": "case-29",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        request_id = conn.execute(
            "INSERT INTO monitoring_alert_review_requests "
            "(alert_id, evidence_ref, transition_evidence) VALUES (?, ?, ?)",
            (alert_id, "legacy-reference-preserved", payload),
        ).lastrowid
        row = conn.execute(
            "SELECT evidence_ref, transition_evidence "
            "FROM monitoring_alert_review_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        assert row["evidence_ref"] == "legacy-reference-preserved"
        assert row["transition_evidence"] == payload
    finally:
        conn.rollback()
        conn.close()


def _legacy_sqlite_wrapper():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """
        CREATE TABLE monitoring_alert_review_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL,
            evidence_ref TEXT,
            state TEXT NOT NULL DEFAULT 'pending'
        );
        INSERT INTO monitoring_alert_review_requests
            (alert_id, evidence_ref, state)
        VALUES
            (41, 'existing-reference', 'pending');
        """
    )
    raw.commit()
    return db_module.DBConnection(raw, is_postgres=False)


def test_existing_sqlite_upgrade_is_additive_preserves_rows_and_is_idempotent():
    db = _legacy_sqlite_wrapper()
    try:
        before = db.execute(
            "SELECT id, alert_id, evidence_ref, state "
            "FROM monitoring_alert_review_requests"
        ).fetchone()

        first = db_module._ensure_monitoring_review_transition_evidence_schema(db)
        after = db.execute(
            "SELECT id, alert_id, evidence_ref, state, transition_evidence "
            "FROM monitoring_alert_review_requests"
        ).fetchone()
        assert first == {
            "engine": "sqlite",
            "changed": True,
            "index_changed": True,
            "nullable": True,
            "default": None,
        }
        assert {
            key: after[key]
            for key in ("id", "alert_id", "evidence_ref", "state")
        } == dict(before)
        assert after["transition_evidence"] is None

        payload = '{"edd_case_id":"edd-41"}'
        db.execute(
            "UPDATE monitoring_alert_review_requests "
            "SET transition_evidence = ? WHERE id = ?",
            (payload, after["id"]),
        )
        second = db_module._ensure_monitoring_review_transition_evidence_schema(db)
        preserved = db.execute(
            "SELECT evidence_ref, transition_evidence "
            "FROM monitoring_alert_review_requests WHERE id = ?",
            (after["id"],),
        ).fetchone()
        assert second["changed"] is False
        assert preserved == {
            "evidence_ref": "existing-reference",
            "transition_evidence": payload,
        }
    finally:
        db.close()


def test_migration_file_replays_idempotently_on_legacy_sqlite():
    db = _legacy_sqlite_wrapper()
    try:
        sql = MIGRATION.read_text(encoding="utf-8")
        db.executescript(sql)
        db.executescript(sql)
        columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(monitoring_alert_review_requests)"
            ).fetchall()
        }
        row = db.execute(
            "SELECT evidence_ref, transition_evidence "
            "FROM monitoring_alert_review_requests"
        ).fetchone()
        assert "transition_evidence" in columns
        assert row == {
            "evidence_ref": "existing-reference",
            "transition_evidence": None,
        }
    finally:
        db.close()


def test_startup_installs_column_before_marking_migration_covered():
    source = (BACKEND / "db.py").read_text(encoding="utf-8")
    init_body = source[
        source.index("def init_db():"):
        source.index("def _mark_known_migrations_as_applied")
    ]
    schema_commit = init_body.index(
        "startup: schema DDL committed — Database schema initialized"
    )
    install = init_body.index(
        "_ensure_monitoring_review_transition_evidence_schema(db)"
    )
    ledger = init_body.index("_mark_known_migrations_as_applied(db)")
    assert schema_commit < install < ledger
    assert "055" not in db_module.FILE_MIGRATIONS_REQUIRING_RUNNER


def test_migration_055_is_additive_only_and_has_narrow_rollback():
    text = MIGRATION.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    normalized = " ".join(executable.split()).upper()
    assert normalized == (
        "ALTER TABLE MONITORING_ALERT_REVIEW_REQUESTS "
        "ADD COLUMN IF NOT EXISTS TRANSITION_EVIDENCE TEXT; "
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "UQ_MONITORING_REVIEW_REQUESTS_ONE_PENDING "
        "ON MONITORING_ALERT_REVIEW_REQUESTS(ALERT_ID) "
        "WHERE STATE = 'PENDING';"
    )
    assert "UPDATE " not in normalized
    assert "DELETE " not in normalized
    assert "INSERT " not in normalized
    assert "DROP COLUMN IF EXISTS transition_evidence" in text
    assert "DROP INDEX IF EXISTS uq_monitoring_review_requests_one_pending" in text
    assert "evidence_ref" in text


def test_sqlite_partial_unique_index_rejects_second_pending_request():
    db = _legacy_sqlite_wrapper()
    try:
        db_module._ensure_monitoring_review_transition_evidence_schema(db)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO monitoring_alert_review_requests "
                "(alert_id, evidence_ref, state) VALUES (?, ?, 'pending')",
                (41, "second-pending"),
            )
        db.rollback()
        rows = db.execute(
            "SELECT alert_id, state FROM monitoring_alert_review_requests"
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {"alert_id": 41, "state": "pending"}
        ]
    finally:
        db.close()


def test_schema_upgrade_fails_closed_on_duplicate_pending_rows():
    db = _legacy_sqlite_wrapper()
    try:
        db.execute(
            "INSERT INTO monitoring_alert_review_requests "
            "(alert_id, evidence_ref, state) VALUES (?, ?, 'pending')",
            (41, "duplicate-before-upgrade"),
        )
        with pytest.raises(RuntimeError, match="multiple pending"):
            db_module._ensure_monitoring_review_transition_evidence_schema(db)
        indexes = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name = ?",
            (db_module.MONITORING_PENDING_REVIEW_UNIQUE_INDEX,),
        ).fetchall()
        assert indexes == []
        columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(monitoring_alert_review_requests)"
            ).fetchall()
        }
        assert "transition_evidence" not in columns
        rows = db.execute(
            "SELECT alert_id, state, evidence_ref "
            "FROM monitoring_alert_review_requests ORDER BY id"
        ).fetchall()
        assert rows == [
            {
                "alert_id": 41,
                "state": "pending",
                "evidence_ref": "existing-reference",
            },
            {
                "alert_id": 41,
                "state": "pending",
                "evidence_ref": "duplicate-before-upgrade",
            },
        ]
    finally:
        db.close()


def test_schema_manifest_tracks_only_the_new_review_request_column():
    import schema_drift

    expected = schema_drift.load_expected()
    assert (
        "transition_evidence"
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
        self.index_definition = None
        self.index_unique = True
        self.index_valid = True
        self.index_ready = True
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        normalized = " ".join(sql.split())
        if "FROM information_schema.tables" in normalized:
            return _Result([{"exists": 1}])
        if "FROM information_schema.columns" in normalized:
            return _Result([self.column] if self.column else [])
        if normalized.startswith(
            "SELECT alert_id, COUNT(*) AS pending_count"
        ):
            return _Result([])
        if "FROM pg_index i" in normalized:
            return _Result(
                [{
                    "indexdef": self.index_definition,
                    "indisunique": self.index_unique,
                    "indisvalid": self.index_valid,
                    "indisready": self.index_ready,
                }]
                if self.index_definition
                else []
            )
        if normalized.startswith(
            "ALTER TABLE monitoring_alert_review_requests ADD COLUMN"
        ):
            self.column = {
                "data_type": "text",
                "is_nullable": "YES",
                "column_default": None,
            }
            return _Result([])
        if normalized.startswith(
            "CREATE UNIQUE INDEX uq_monitoring_review_requests_one_pending"
        ):
            self.index_definition = (
                "CREATE UNIQUE INDEX uq_monitoring_review_requests_one_pending "
                "ON monitoring_alert_review_requests USING btree (alert_id) "
                "WHERE (state = 'pending'::text)"
            )
            return _Result([])
        raise AssertionError(f"unexpected SQL: {normalized}")


def test_postgres_upgrade_uses_native_additive_text_column_and_is_idempotent():
    fake = _FakePostgres()
    first = db_module._ensure_monitoring_review_transition_evidence_schema(fake)
    second = db_module._ensure_monitoring_review_transition_evidence_schema(fake)

    alters = [
        " ".join(sql.split())
        for sql, _params in fake.calls
        if sql.lstrip().upper().startswith("ALTER TABLE")
    ]
    assert first["engine"] == "postgresql"
    assert first["changed"] is True
    assert first["index_changed"] is True
    assert second["changed"] is False
    assert second["index_changed"] is False
    assert alters == [
        "ALTER TABLE monitoring_alert_review_requests "
        "ADD COLUMN transition_evidence TEXT"
    ]
    index_reads = [
        params
        for sql, params in fake.calls
        if "FROM pg_index i" in " ".join(sql.split())
    ]
    assert index_reads
    assert all(
        params
        == (
            "monitoring_alert_review_requests",
            db_module.MONITORING_PENDING_REVIEW_UNIQUE_INDEX,
        )
        for params in index_reads
    )


@pytest.mark.parametrize(
    "definition",
    (
        "CREATE UNIQUE INDEX uq_monitoring_review_requests_one_pending "
        "ON wrong_table (alert_id) WHERE state = 'pending'",
        "CREATE UNIQUE INDEX uq_monitoring_review_requests_one_pending "
        "ON monitoring_alert_review_requests (other, alert_id) "
        "WHERE state = 'pending'",
        "CREATE UNIQUE INDEX uq_monitoring_review_requests_one_pending "
        "ON monitoring_alert_review_requests (alert_id) "
        "WHERE state = 'pending' AND active = 1",
        "CREATE INDEX uq_monitoring_review_requests_one_pending "
        "ON monitoring_alert_review_requests (alert_id) "
        "WHERE state = 'pending'",
    ),
)
def test_pending_review_index_validator_rejects_fail_open_shapes(definition):
    assert (
        db_module._is_valid_monitoring_pending_review_index(definition)
        is False
    )


@pytest.mark.parametrize(
    ("attribute", "value"),
    (
        ("index_unique", False),
        ("index_valid", False),
        ("index_ready", False),
    ),
)
def test_postgres_upgrade_rejects_non_enforcing_index_metadata(
    attribute,
    value,
):
    fake = _FakePostgres()
    fake.column = {
        "data_type": "text",
        "is_nullable": "YES",
        "column_default": None,
    }
    fake.index_definition = (
        "CREATE UNIQUE INDEX uq_monitoring_review_requests_one_pending "
        "ON monitoring_alert_review_requests USING btree (alert_id) "
        "WHERE (state = 'pending'::text)"
    )
    setattr(fake, attribute, value)

    with pytest.raises(RuntimeError, match="unexpected definition"):
        db_module._ensure_monitoring_review_transition_evidence_schema(fake)


def _postgres_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get(
        "DATABASE_URL_TEST"
    )


@pytest.mark.skipif(not _postgres_dsn(), reason="No disposable PostgreSQL test DSN")
def test_live_postgres_additive_upgrade_preserves_existing_row():
    dsn = _postgres_dsn()
    parts = urlsplit(dsn)
    if parts.hostname not in (None, "localhost", "127.0.0.1", "::1"):
        pytest.skip("PostgreSQL migration test requires a local disposable server")
    if "test" not in (parts.path or "").lower():
        pytest.skip("PostgreSQL migration test requires a test-named database")

    import psycopg2

    schema = f"monitoring_transition_evidence_{uuid.uuid4().hex[:12]}"
    raw = psycopg2.connect(dsn)
    db = db_module.DBConnection(raw, is_postgres=True, database_identity=dsn)
    try:
        db.execute(f'CREATE SCHEMA "{schema}"')
        db.execute(f'SET search_path TO "{schema}"')
        db.execute(
            "CREATE TABLE monitoring_alert_review_requests ("
            "id SERIAL PRIMARY KEY, alert_id INTEGER NOT NULL, "
            "evidence_ref TEXT, state TEXT NOT NULL DEFAULT 'pending')"
        )
        db.execute(
            "INSERT INTO monitoring_alert_review_requests "
            "(alert_id, evidence_ref) VALUES (?, ?)",
            (77, "existing-reference"),
        )
        db.commit()

        first = db_module._ensure_monitoring_review_transition_evidence_schema(db)
        db.commit()
        second = db_module._ensure_monitoring_review_transition_evidence_schema(db)
        row = db.execute(
            "SELECT evidence_ref, transition_evidence "
            "FROM monitoring_alert_review_requests"
        ).fetchone()
        metadata = (
            db_module._monitoring_review_transition_evidence_column_metadata(db)
        )
        assert first["changed"] is True
        assert second["changed"] is False
        assert first["index_changed"] is True
        assert second["index_changed"] is False
        assert row == {
            "evidence_ref": "existing-reference",
            "transition_evidence": None,
        }
        assert metadata == {
            "data_type": "text",
            "is_nullable": "YES",
            "column_default": None,
        }
        with pytest.raises(Exception) as conflict:
            db.execute(
                "INSERT INTO monitoring_alert_review_requests "
                "(alert_id, evidence_ref, state) VALUES (?, ?, 'pending')",
                (77, "second-pending"),
            )
        assert getattr(conflict.value, "pgcode", None) == "23505"
    finally:
        try:
            db.rollback()
            db.execute("SET search_path TO public")
            db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            db.commit()
        finally:
            db.close()
