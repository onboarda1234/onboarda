"""Database hardening for the canonical Monitoring Alert status vocabulary."""

import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_module


BACKEND = Path(__file__).resolve().parents[1]
EXPECTED_STATUSES = (
    "open",
    "triaged",
    "assigned",
    "in_review",
    "escalated",
    "routed_to_review",
    "routed_to_edd",
    "dismissed",
    "resolved",
    "waived",
)


def _monitoring_table_blocks():
    source = (BACKEND / "db.py").read_text(encoding="utf-8")
    return re.findall(
        r"CREATE TABLE IF NOT EXISTS monitoring_alerts \((.*?)\n    \);",
        source,
        re.DOTALL,
    )


def test_canonical_vocabulary_and_both_fresh_schemas_are_in_lockstep():
    assert db_module.MONITORING_ALERT_STATUS_VALUES == EXPECTED_STATUSES
    assert (
        db_module.MONITORING_ALERT_STATUS_CONSTRAINT
        == "monitoring_alerts_status_check"
    )

    blocks = _monitoring_table_blocks()
    assert len(blocks) == 2, "expected PostgreSQL and SQLite table definitions"
    pattern = re.compile(
        r"status TEXT NOT NULL DEFAULT 'open'\s+"
        r"CONSTRAINT monitoring_alerts_status_check\s+"
        r"CHECK\(status IN \((.*?)\)\)",
        re.DOTALL,
    )
    for block in blocks:
        match = pattern.search(block)
        assert match, "fresh schema must use the named NOT NULL status CHECK"
        assert tuple(re.findall(r"'([^']+)'", match.group(1))) == EXPECTED_STATUSES
        assert not re.search(
            r"CHECK\s*\([^)]*status[^)]*resolved_at", block, re.IGNORECASE
        ), "status hardening must not infer lifecycle truth from resolved_at"


def test_fresh_sqlite_accepts_exact_canon_and_rejects_null_or_unknown(temp_db):
    conn = sqlite3.connect(temp_db)
    try:
        for status in EXPECTED_STATUSES:
            conn.execute(
                "INSERT INTO monitoring_alerts (status, summary) VALUES (?, ?)",
                (status, f"constraint probe: {status}"),
            )

        cursor = conn.execute(
            "INSERT INTO monitoring_alerts (summary) VALUES (?)",
            ("constraint default probe",),
        )
        defaulted = conn.execute(
            "SELECT status FROM monitoring_alerts WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        assert defaulted == ("open",)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO monitoring_alerts (status) VALUES (?)",
                ("closed",),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO monitoring_alerts (status) VALUES (NULL)")
    finally:
        conn.rollback()
        conn.close()


def _sqlite_wrapper_with_status_rows(*statuses):
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute(
        "CREATE TABLE monitoring_alerts "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT DEFAULT 'open')"
    )
    for status in statuses:
        raw.execute(
            "INSERT INTO monitoring_alerts (status) VALUES (?)",
            (status,),
        )
    raw.commit()
    return db_module.DBConnection(raw, is_postgres=False)


def test_long_lived_sqlite_scan_is_read_only_when_rows_are_compatible():
    db = _sqlite_wrapper_with_status_rows("open", "resolved")
    try:
        before = [
            tuple(row)
            for row in db.execute(
                "SELECT id, status FROM monitoring_alerts ORDER BY id"
            ).fetchall()
        ]
        result = db_module._ensure_monitoring_alert_status_constraint(db)
        after = [
            tuple(row)
            for row in db.execute(
                "SELECT id, status FROM monitoring_alerts ORDER BY id"
            ).fetchall()
        ]
        assert result == {
            "engine": "sqlite",
            "compatible_rows": True,
            "constraint_enforced": False,
            "changed": False,
        }
        assert after == before
    finally:
        db.close()


@pytest.mark.parametrize("status", [None, "", " ", "closed", "RESOLVED"])
def test_compatibility_scan_fails_closed_without_rewriting(status):
    db = _sqlite_wrapper_with_status_rows(status)
    try:
        with pytest.raises(RuntimeError, match="non-canonical rows"):
            db_module._ensure_monitoring_alert_status_constraint(db)
        row = db.execute(
            "SELECT status FROM monitoring_alerts WHERE id = 1"
        ).fetchone()
        assert row["status"] == status
    finally:
        db.close()


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakePostgres:
    is_postgres = True

    def __init__(
        self,
        *,
        offenders=None,
        constraint=None,
        nullable="YES",
    ):
        self.offenders = list(offenders or [])
        self.constraint = constraint
        self.nullable = nullable
        self.calls = []

    @staticmethod
    def _canonical_definition():
        values = ", ".join(
            f"'{value}'::text" for value in EXPECTED_STATUSES
        )
        return f"CHECK ((status = ANY (ARRAY[{values}])))"

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        normalized = " ".join(sql.split())
        if "FROM information_schema.columns" in normalized:
            return _Result(
                [
                    {
                        "is_nullable": self.nullable,
                        "column_default": "'open'::text",
                    }
                ]
            )
        if (
            "FROM monitoring_alerts" in normalized
            and "COUNT(*) AS row_count" in normalized
        ):
            return _Result(self.offenders)
        if "FROM pg_constraint c" in normalized:
            return _Result([self.constraint] if self.constraint else [])
        if "DROP CONSTRAINT" in normalized:
            self.constraint = None
            return _Result([])
        if "ADD CONSTRAINT" in normalized:
            self.constraint = {
                "conname": db_module.MONITORING_ALERT_STATUS_CONSTRAINT,
                "definition": self._canonical_definition(),
                "convalidated": False,
            }
            return _Result([])
        if "VALIDATE CONSTRAINT" in normalized:
            self.constraint["convalidated"] = True
            return _Result([])
        if "ALTER COLUMN" in normalized and "SET NOT NULL" in normalized:
            self.nullable = "NO"
            return _Result([])
        return _Result([])


def _alter_statements(fake):
    return [
        " ".join(sql.split())
        for sql, _params in fake.calls
        if sql.lstrip().upper().startswith("ALTER TABLE")
    ]


def test_postgres_installs_not_valid_then_validates_and_is_idempotent():
    fake = _FakePostgres()
    result = db_module._ensure_monitoring_alert_status_constraint(fake)
    assert result["changed"] is True
    alters = _alter_statements(fake)
    assert len(alters) == 3
    assert "ADD CONSTRAINT \"monitoring_alerts_status_check\"" in alters[0]
    assert alters[0].endswith("NOT VALID")
    assert "VALIDATE CONSTRAINT \"monitoring_alerts_status_check\"" in alters[1]
    assert 'ALTER COLUMN "status" SET NOT NULL' in alters[2]

    call_count = len(fake.calls)
    second = db_module._ensure_monitoring_alert_status_constraint(fake)
    assert second["changed"] is False
    assert not _alter_statements(
        type("CallsOnly", (), {"calls": fake.calls[call_count:]})()
    )


def test_postgres_off_canon_preflight_propagates_before_any_ddl():
    fake = _FakePostgres(
        offenders=[{"status_value": "legacy_open", "row_count": 2}]
    )
    with pytest.raises(RuntimeError, match="legacy_open"):
        db_module._ensure_monitoring_alert_status_constraint(fake)
    assert _alter_statements(fake) == []


def test_postgres_refuses_to_drop_unexpected_status_constraint():
    fake = _FakePostgres(
        constraint={
            "conname": "legacy_status_business_rule",
            "definition": "CHECK ((status <> 'forbidden'::text))",
            "convalidated": True,
        }
    )
    with pytest.raises(RuntimeError, match="unexpected PostgreSQL CHECK"):
        db_module._ensure_monitoring_alert_status_constraint(fake)
    assert _alter_statements(fake) == []


def test_postgres_constraint_match_validates_expression_not_just_literals():
    canonical = _FakePostgres._canonical_definition()
    assert db_module._postgres_monitoring_status_check_is_canonical(canonical)
    assert not db_module._postgres_monitoring_status_check_is_canonical(
        canonical.replace(" = ANY ", " <> ALL ")
    )
    assert not db_module._postgres_monitoring_status_check_is_canonical(
        canonical.replace(
            ")))",
            ") OR status IS NULL))",
        )
    )


def test_wrong_named_constraint_is_replaced_atomically_with_exact_canon():
    fake = _FakePostgres(
        constraint={
            "conname": db_module.MONITORING_ALERT_STATUS_CONSTRAINT,
            "definition": "CHECK ((status = ANY (ARRAY['open'::text])))",
            "convalidated": True,
        }
    )
    result = db_module._ensure_monitoring_alert_status_constraint(fake)
    assert result["changed"] is True
    alters = _alter_statements(fake)
    assert "DROP CONSTRAINT \"monitoring_alerts_status_check\"" in alters[0]
    assert alters[1].endswith("NOT VALID")
    assert "VALIDATE CONSTRAINT" in alters[2]
    assert "SET NOT NULL" in alters[3]


def test_startup_installs_constraint_before_marking_migration_covered():
    source = (BACKEND / "db.py").read_text(encoding="utf-8")
    init_body = source[
        source.index("def init_db():"):
        source.index("def _mark_known_migrations_as_applied")
    ]
    schema_commit = init_body.index(
        "startup: schema DDL committed — Database schema initialized"
    )
    install = init_body.index("_ensure_monitoring_alert_status_constraint(db)")
    ledger = init_body.index("_mark_known_migrations_as_applied(db)")
    assert schema_commit < install < ledger


def test_migration_054_is_a_no_rewrite_marker_with_narrow_rollback():
    path = (
        BACKEND
        / "migrations"
        / "scripts"
        / "migration_054_monitoring_alert_status_constraint.sql"
    )
    text = path.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    assert executable.strip() == "SELECT 1;"
    assert "DROP CONSTRAINT IF EXISTS monitoring_alerts_status_check" in text
    assert "ALTER COLUMN status DROP NOT NULL" in text
    assert "no data" in text.lower() or "no row" in text.lower()


def _postgres_dsn():
    return os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL_TEST")


@pytest.mark.skipif(not _postgres_dsn(), reason="No disposable PostgreSQL test DSN")
def test_live_postgres_constraint_and_not_null_enforcement():
    dsn = _postgres_dsn()
    parts = urlsplit(dsn)
    if parts.hostname not in (None, "localhost", "127.0.0.1", "::1"):
        pytest.skip("PostgreSQL constraint test requires a local disposable server")
    if "test" not in (parts.path or "").lower():
        pytest.skip("PostgreSQL constraint test requires a test-named database")

    import psycopg2

    schema = f"monitoring_status_{uuid.uuid4().hex[:12]}"
    raw = psycopg2.connect(dsn)
    db = db_module.DBConnection(raw, is_postgres=True, database_identity=dsn)
    try:
        db.execute(f'CREATE SCHEMA "{schema}"')
        db.execute(f'SET search_path TO "{schema}"')
        db.execute(
            "CREATE TABLE monitoring_alerts "
            "(id SERIAL PRIMARY KEY, status TEXT DEFAULT 'open')"
        )

        db.execute(
            "INSERT INTO monitoring_alerts (status) VALUES (?)",
            ("legacy_open",),
        )
        db.commit()
        with pytest.raises(RuntimeError, match="legacy_open"):
            db_module._ensure_monitoring_alert_status_constraint(db)
        preserved = db.execute(
            "SELECT status FROM monitoring_alerts WHERE id = 1"
        ).fetchone()
        assert preserved["status"] == "legacy_open"
        assert db_module._postgres_monitoring_alert_status_constraints(db) == []

        # Test-fixture reconciliation only: the production helper above made
        # no rewrite and installed no DDL while the row was incompatible.
        db.execute(
            "UPDATE monitoring_alerts SET status = ? WHERE id = 1",
            ("open",),
        )
        for status in EXPECTED_STATUSES[1:]:
            db.execute(
                "INSERT INTO monitoring_alerts (status) VALUES (?)",
                (status,),
            )
        db.commit()

        result = db_module._ensure_monitoring_alert_status_constraint(db)
        db.commit()
        assert result["constraint_enforced"] is True

        with pytest.raises(Exception):
            db.execute(
                "INSERT INTO monitoring_alerts (status) VALUES (?)",
                ("closed",),
            )
        with pytest.raises(Exception):
            db.execute("INSERT INTO monitoring_alerts (status) VALUES (NULL)")
        db.rollback()

        constraint = db_module._postgres_monitoring_alert_status_constraints(db)
        metadata = db_module._monitoring_alert_status_column_metadata(db)
        assert len(constraint) == 1
        assert constraint[0]["convalidated"] is True
        assert metadata["is_nullable"] == "NO"
    finally:
        try:
            db.rollback()
            db.execute('SET search_path TO public')
            db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            db.commit()
        finally:
            db.close()
