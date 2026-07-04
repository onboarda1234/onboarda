import contextlib
import importlib
import os
import sqlite3
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supervisor.audit import append_verdict_chain_entry, AuditLogger


@contextlib.contextmanager
def _isolated_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "supervisor_audit_repair.db")
    orig_database_url = os.environ.get("DATABASE_URL")
    orig_environment = os.environ.get("ENVIRONMENT")
    orig_db_path = os.environ.get("DB_PATH")

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", db_file)

    import config as config_module
    import db as db_module

    importlib.reload(config_module)
    importlib.reload(db_module)
    try:
        yield db_module
    finally:
        db_module.close_pg_pool()
        for var, value in (
            ("DATABASE_URL", orig_database_url),
            ("ENVIRONMENT", orig_environment),
            ("DB_PATH", orig_db_path),
        ):
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        importlib.reload(config_module)
        importlib.reload(db_module)


def _column_types(db):
    rows = db.execute("PRAGMA table_info(supervisor_audit_log)").fetchall()
    return {row["name"]: str(row["type"]).upper() for row in rows}


def test_fresh_db_has_supervisor_audit_severity(tmp_path, monkeypatch):
    with _isolated_db(tmp_path, monkeypatch) as db_module:
        db_module.init_db()
        db = db_module.get_db()
        try:
            columns = _column_types(db)
            assert columns["id"] == "TEXT"
            assert "severity" in columns
            assert "actor_type" in columns
            assert "detail" in columns
            assert "data_json" in columns
            assert "previous_hash" in columns
        finally:
            db.close()


def test_supervisor_audit_schema_repair_backfills_legacy_rows(tmp_path, monkeypatch):
    with _isolated_db(tmp_path, monkeypatch) as db_module:
        db = db_module.get_db()
        try:
            db.executescript(
                """
                CREATE TABLE supervisor_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT (datetime('now')),
                    event_type TEXT NOT NULL,
                    application_id TEXT,
                    pipeline_id TEXT,
                    agent_type TEXT,
                    actor TEXT,
                    action TEXT NOT NULL,
                    details TEXT DEFAULT '{}',
                    prev_hash TEXT,
                    entry_hash TEXT
                );
                INSERT INTO supervisor_audit_log
                    (event_type, application_id, pipeline_id, actor, action, details, entry_hash)
                VALUES
                    ('pipeline_completed', 'legacy-app', 'pipe-1', 'legacy-agent',
                     'Legacy supervisor event', '{"legacy": true}', 'legacyhash');
                """
            )
            db.commit()

            db_module._ensure_supervisor_audit_log_schema(db)
            db.commit()

            columns = _column_types(db)
            assert columns["id"] == "TEXT"
            assert "severity" in columns
            assert "actor_type" in columns
            assert "actor_id" in columns
            assert "detail" in columns
            assert "data_json" in columns
            assert "previous_hash" in columns

            legacy = db.execute(
                "SELECT * FROM supervisor_audit_log WHERE application_id = ?",
                ("legacy-app",),
            ).fetchone()
            assert legacy["id"] == "1"
            assert legacy["severity"] == "info"
            assert legacy["detail"] == '{"legacy": true}'
            assert legacy["actor_id"] == "legacy-agent"
            assert legacy["entry_hash"] != "legacyhash"

            append_verdict_chain_entry(
                db=db,
                application_id="modern-app",
                verdict="CONSISTENT",
                contradiction_count=0,
                supervisor_confidence=0.99,
                memo_id="memo-modern",
                actor_id="co-1",
                actor_name="Compliance Officer",
                actor_role="co",
            )
            db.commit()

            modern = db.execute(
                "SELECT * FROM supervisor_audit_log WHERE application_id = ?",
                ("modern-app",),
            ).fetchone()
            assert modern["severity"] == "info"
            assert modern["actor_type"] == "officer"

            verifier = AuditLogger(db_path=str(tmp_path / "supervisor_audit_repair.db"))
            result = verifier.verify_chain_integrity(limit=10)
            assert result["verified"] is True
            assert result["entries_checked"] == 2
        finally:
            db.close()


def test_supervisor_audit_migration_preserves_and_seals_legacy(tmp_path, monkeypatch):
    """audit finding B2: the legacy chain must be archived (not dropped) and sealed.

    Rehashing legacy rows into the modern schema must not silently destroy the
    original chain — otherwise a pre-migration tamper is re-sealed into a clean
    chain undetectably. The original table must be preserved and a seal over the
    ordered legacy (id, entry_hash) sequence recorded.
    """
    import hashlib

    with _isolated_db(tmp_path, monkeypatch) as db_module:
        db = db_module.get_db()
        try:
            db.executescript(
                """
                CREATE TABLE supervisor_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    event_type TEXT NOT NULL,
                    application_id TEXT,
                    pipeline_id TEXT,
                    agent_type TEXT,
                    actor TEXT,
                    action TEXT NOT NULL,
                    details TEXT DEFAULT '{}',
                    prev_hash TEXT,
                    entry_hash TEXT
                );
                INSERT INTO supervisor_audit_log
                    (timestamp, event_type, application_id, actor, action, details, entry_hash)
                VALUES
                    ('2026-01-01T00:00:01', 'pipeline_completed', 'app-a', 'agent-1', 'Event A', '{}', 'hashA'),
                    ('2026-01-01T00:00:02', 'pipeline_completed', 'app-b', 'agent-2', 'Event B', '{}', 'hashB');
                """
            )
            db.commit()

            db_module._ensure_supervisor_audit_log_schema(db)
            db.commit()

            # 1. A migration record must exist, counting both legacy rows.
            rec = db.execute("SELECT * FROM supervisor_audit_migrations").fetchall()
            assert len(rec) == 1
            rec = dict(rec[0])
            assert rec["legacy_row_count"] == 2

            # 2. The archived original table must still exist with the ORIGINAL
            #    entry hashes intact (evidence preserved, not destroyed).
            archive_table = rec["archive_table"]
            assert archive_table.startswith("supervisor_audit_log_legacy_")
            archived = db.execute(
                f"SELECT entry_hash FROM {archive_table} ORDER BY timestamp ASC"
            ).fetchall()
            assert [r["entry_hash"] for r in archived] == ["hashA", "hashB"]

            # 3. The recorded seal must equal a seal recomputed from the archive,
            #    so any later mutation of the archive is detectable.
            expected_seal = hashlib.sha256(
                "\n".join(["1:hashA", "2:hashB"]).encode()
            ).hexdigest()
            assert rec["legacy_chain_seal"] == expected_seal

            # 4. The live chain still holds exactly the migrated rows (re-hashed).
            live = db.execute(
                "SELECT entry_hash FROM supervisor_audit_log ORDER BY timestamp ASC"
            ).fetchall()
            assert len(live) == 2
            assert all(r["entry_hash"] not in ("hashA", "hashB") for r in live)
        finally:
            db.close()


def test_migration_026_marker_is_sqlite_safe(tmp_path, monkeypatch):
    with _isolated_db(tmp_path, monkeypatch):
        migration = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "migrations",
            "scripts",
            "migration_026_supervisor_audit_schema_repair.sql",
        )
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(open(migration, encoding="utf-8").read())
        finally:
            conn.close()
