import os
import sqlite3
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_fk_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            name TEXT,
            role TEXT
        );
        CREATE TABLE applications (
            id TEXT PRIMARY KEY,
            ref TEXT,
            company_name TEXT,
            risk_level TEXT,
            final_risk_level TEXT,
            risk_score REAL,
            status TEXT,
            updated_at TEXT
        );
        CREATE TABLE edd_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id TEXT,
            client_name TEXT,
            risk_level TEXT,
            risk_score REAL,
            stage TEXT,
            assigned_officer TEXT REFERENCES users(id),
            trigger_source TEXT,
            trigger_notes TEXT,
            edd_notes TEXT
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            user_name TEXT,
            user_role TEXT,
            action TEXT,
            target TEXT,
            detail TEXT,
            ip_address TEXT
        );
        """
    )
    return conn


def _insert_app(conn, app_id="app-edd-fk"):
    conn.execute(
        "INSERT INTO applications (id, ref, company_name, final_risk_level, risk_level, risk_score, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (app_id, "ARF-EDD-FK", "EDD FK Ltd", "HIGH", "HIGH", 76, "pricing_review"),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())


def _routing():
    return {
        "route": "edd",
        "policy_version": "edd_routing_policy_v1",
        "triggers": ["high_or_very_high_risk"],
        "evaluated_at": "2026-05-10T00:00:00Z",
    }


def test_client_triggered_edd_actuation_leaves_assigned_officer_null():
    from edd_actuation import actuate_edd_routing

    conn = _make_fk_db()
    app_row = _insert_app(conn)

    result = actuate_edd_routing(
        conn,
        app_row,
        _routing(),
        {},
        {"sub": "client001", "name": "Portal Client", "role": "client"},
    )
    conn.commit()

    assert result["created"] is True
    case = conn.execute("SELECT assigned_officer FROM edd_cases WHERE id=?", (result["case_id"],)).fetchone()
    assert case["assigned_officer"] is None


def test_system_triggered_edd_actuation_leaves_assigned_officer_null():
    from edd_actuation import actuate_edd_routing

    conn = _make_fk_db()
    app_row = _insert_app(conn)

    result = actuate_edd_routing(conn, app_row, _routing(), {}, None)
    conn.commit()

    assert result["created"] is True
    case = conn.execute("SELECT assigned_officer FROM edd_cases WHERE id=?", (result["case_id"],)).fetchone()
    assert case["assigned_officer"] is None


def test_officer_triggered_edd_actuation_assigns_only_existing_officer():
    from edd_actuation import actuate_edd_routing

    conn = _make_fk_db()
    conn.execute("INSERT INTO users (id, name, role) VALUES (?,?,?)", ("co001", "CO One", "co"))
    app_row = _insert_app(conn)

    result = actuate_edd_routing(
        conn,
        app_row,
        _routing(),
        {},
        {"sub": "co001", "name": "CO One", "role": "co"},
    )
    conn.commit()

    assert result["created"] is True
    case = conn.execute("SELECT assigned_officer FROM edd_cases WHERE id=?", (result["case_id"],)).fetchone()
    assert case["assigned_officer"] == "co001"


def test_duplicate_edd_actuation_reuses_active_case():
    from edd_actuation import actuate_edd_routing

    conn = _make_fk_db()
    app_row = _insert_app(conn)

    first = actuate_edd_routing(conn, app_row, _routing(), {}, None)
    second = actuate_edd_routing(conn, app_row, _routing(), {}, None)
    conn.commit()

    assert first["created"] is True
    assert second["created"] is False
    assert second["case_id"] == first["case_id"]
    assert conn.execute("SELECT COUNT(*) AS c FROM edd_cases").fetchone()["c"] == 1
