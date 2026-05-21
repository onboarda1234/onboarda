import importlib
import os
import sqlite3
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_db_module(db_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DB_PATH", db_path)

    import config as config_module
    import db as db_module

    importlib.reload(config_module)
    db_module = importlib.reload(db_module)
    db_module.DB_PATH = db_path
    db_module.DATABASE_URL = ""
    db_module.USE_POSTGRESQL = False
    db_module._pg_pool = None
    return db_module


def _open_sqlite(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _column_names(conn, table):
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _index_names(conn):
    return {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }


def _create_legacy_lifecycle_tables(conn):
    conn.execute(
        """
        CREATE TABLE applications (
            id TEXT PRIMARY KEY,
            client_id TEXT,
            status TEXT,
            assigned_to TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE edd_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT NOT NULL REFERENCES applications(id),
            client_name TEXT NOT NULL,
            risk_level TEXT,
            risk_score REAL,
            stage TEXT DEFAULT 'triggered',
            assigned_officer TEXT REFERENCES users(id),
            senior_reviewer TEXT REFERENCES users(id),
            trigger_source TEXT DEFAULT 'officer_decision',
            trigger_notes TEXT,
            edd_notes TEXT DEFAULT '[]',
            decision TEXT,
            decision_reason TEXT,
            decided_by TEXT REFERENCES users(id),
            decided_at TEXT,
            triggered_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE periodic_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
            client_name TEXT,
            risk_level TEXT,
            last_review_date TEXT,
            next_review_date TEXT,
            trigger_type TEXT,
            trigger_reason TEXT,
            previous_risk_level TEXT,
            new_risk_level TEXT,
            review_memo TEXT,
            status TEXT DEFAULT 'pending',
            due_date TEXT,
            started_at TEXT,
            completed_at TEXT,
            assigned_officer TEXT REFERENCES users(id),
            assigned_by TEXT REFERENCES users(id),
            reassigned_reason TEXT,
            decision TEXT,
            decision_reason TEXT,
            outcome TEXT,
            outcome_reason TEXT,
            outcome_recorded_at TEXT,
            review_cycle_number INTEGER DEFAULT 1,
            review_type TEXT,
            policy_version TEXT,
            frequency_months INTEGER,
            calculation_basis TEXT,
            legacy_import INTEGER DEFAULT 0,
            legacy_source_type TEXT,
            legacy_source_note TEXT,
            legacy_confidence TEXT,
            legacy_entered_by TEXT REFERENCES users(id),
            legacy_entered_at TEXT,
            legacy_sco_acknowledged_by TEXT REFERENCES users(id),
            legacy_sco_acknowledged_at TEXT,
            import_requires_ack INTEGER DEFAULT 0,
            material_change_attestation TEXT,
            material_change_categories TEXT DEFAULT '[]',
            risk_change_attestation TEXT,
            risk_rerate_reason TEXT,
            risk_rerated_by TEXT REFERENCES users(id),
            risk_rerated_at TEXT,
            officer_rationale TEXT,
            memo_status TEXT,
            periodic_review_memo_id INTEGER,
            required_items TEXT,
            required_items_generated_at TEXT,
            state_changed_at TEXT,
            decided_by TEXT REFERENCES users(id),
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE monitoring_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
            client_name TEXT,
            alert_type TEXT,
            severity TEXT,
            status TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


_MIGRATION_008_EXPECTED_COLUMNS = {
    "edd_cases": {
        "origin_context",
        "linked_monitoring_alert_id",
        "linked_periodic_review_id",
        "assigned_at",
        "escalated_at",
        "closed_at",
        "sla_due_at",
        "priority",
    },
    "periodic_reviews": {
        "trigger_source",
        "linked_monitoring_alert_id",
        "linked_edd_case_id",
        "review_reason",
        "assigned_at",
        "closed_at",
        "sla_due_at",
        "priority",
    },
    "monitoring_alerts": {
        "linked_periodic_review_id",
        "linked_edd_case_id",
        "triaged_at",
        "assigned_at",
        "resolved_at",
    },
}


_MIGRATION_008_EXPECTED_INDEXES = {
    "idx_edd_cases_linked_alert",
    "idx_edd_cases_linked_review",
    "idx_edd_cases_origin_context",
    "idx_periodic_reviews_linked_alert",
    "idx_periodic_reviews_linked_edd",
    "idx_periodic_reviews_trigger_source",
    "idx_monitoring_alerts_linked_edd",
    "idx_monitoring_alerts_linked_review",
}


def test_init_db_repairs_legacy_sqlite_lifecycle_columns(tmp_path, monkeypatch):
    db_path = str(tmp_path / "legacy_lifecycle.db")
    conn = _open_sqlite(db_path)
    try:
        _create_legacy_lifecycle_tables(conn)
    finally:
        conn.close()

    db_module = _load_db_module(db_path, monkeypatch)
    db_module.init_db()

    conn = _open_sqlite(db_path)
    try:
        for table, expected_columns in _MIGRATION_008_EXPECTED_COLUMNS.items():
            assert expected_columns.issubset(_column_names(conn, table))

        index_names = _index_names(conn)
        assert _MIGRATION_008_EXPECTED_INDEXES.issubset(index_names)
    finally:
        conn.close()


def test_init_db_fresh_install_is_idempotent_for_sqlite_preflight(tmp_path, monkeypatch):
    db_path = str(tmp_path / "fresh_install.db")
    db_module = _load_db_module(db_path, monkeypatch)

    db_module.init_db()
    db_module.init_db()

    conn = _open_sqlite(db_path)
    try:
        for table, expected_columns in _MIGRATION_008_EXPECTED_COLUMNS.items():
            assert expected_columns.issubset(_column_names(conn, table))
    finally:
        conn.close()
