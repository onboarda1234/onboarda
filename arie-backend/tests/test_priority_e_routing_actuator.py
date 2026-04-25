"""
tests/test_priority_e_routing_actuator
======================================

Priority E coverage for the deterministic EDD routing policy AND
the new ``apply_routing_decision`` glue + the reconciliation utility.

The pure policy unit cases (A-F) call ``evaluate_edd_routing``
directly and do NOT need a database. The integration cases (G, H, I,
K, J) use a temporary SQLite DB to exercise lane persistence,
edd_cases idempotency, and audit-log emission.

Test labels mirror the Priority E brief:

* A: HIGH final_risk_level routes to EDD
* B: VERY_HIGH final_risk_level routes to EDD
* C: Declared PEP routes to EDD
* D: High-risk sector (crypto / virtual asset) routes to EDD
* E: High-risk / restricted jurisdiction routes to EDD
* F: Medium clean SME stays Standard
* G: Risk recompute Medium -> High creates an EDD case
* H: Idempotency: two evaluations do not duplicate EDD cases
* I: Audit row contains policy_version, triggers, inputs, outcome
* J: Reconciliation detects deliberate drift
* K: Fixture rows excluded from default reconciliation scan
* L: (covered indirectly via approval gate test elsewhere)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)



def _full(**overrides):
    """Return a fact dict that satisfies REQUIRED_FACT_KEYS."""
    base = {
        "final_risk_level": "MEDIUM",
        "declared_pep_present": False,
        "sector_risk_tier": "low",
        "jurisdiction_risk_tier": "low",
        "ownership_transparency_status": "clear",
        "screening_terminality_summary": {},
        "edd_trigger_flags": [],
        "supervisor_mandatory_escalation": False,
        "company_name": "Acme",
        "country": "Mauritius",
        "sector": "Consulting",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- A
def test_a_high_final_risk_routes_to_edd():
    from edd_routing_policy import evaluate_edd_routing, ROUTE_EDD

    decision = evaluate_edd_routing(_full(final_risk_level="HIGH"))
    assert decision["route"] == ROUTE_EDD
    assert "high_or_very_high_risk" in decision["triggers"]


# ---------------------------------------------------------------- B
def test_b_very_high_final_risk_routes_to_edd():
    from edd_routing_policy import evaluate_edd_routing, ROUTE_EDD

    decision = evaluate_edd_routing(_full(final_risk_level="VERY_HIGH"))
    assert decision["route"] == ROUTE_EDD


# ---------------------------------------------------------------- C
def test_c_declared_pep_routes_to_edd_even_at_medium():
    from edd_routing_policy import evaluate_edd_routing, ROUTE_EDD

    decision = evaluate_edd_routing(_full(final_risk_level="MEDIUM", declared_pep_present=True))
    assert decision["route"] == ROUTE_EDD
    assert "declared_pep_present" in decision["triggers"]


# ---------------------------------------------------------------- D
def test_d_crypto_sector_routes_to_edd():
    from edd_routing_policy import evaluate_edd_routing, ROUTE_EDD

    decision = evaluate_edd_routing(_full(final_risk_level="MEDIUM", sector_label="Crypto / Digital Assets Exchange", sector="Crypto / Digital Assets Exchange"))
    assert decision["route"] == ROUTE_EDD
    assert "crypto_or_virtual_asset_sector" in decision["triggers"]


# ---------------------------------------------------------------- E
def test_e_elevated_jurisdiction_routes_to_edd():
    from edd_routing_policy import evaluate_edd_routing, ROUTE_EDD

    decision = evaluate_edd_routing(_full(final_risk_level="MEDIUM", jurisdiction_risk_tier="high", country="British Virgin Islands"))
    assert decision["route"] == ROUTE_EDD
    assert "elevated_jurisdiction" in decision["triggers"]


# ---------------------------------------------------------------- F
def test_f_medium_clean_sme_stays_standard():
    from edd_routing_policy import evaluate_edd_routing, ROUTE_STANDARD

    decision = evaluate_edd_routing(_full(final_risk_level="MEDIUM", sector_label="Management Consulting", sector="Management Consulting"))
    assert decision["route"] == ROUTE_STANDARD
    assert decision["triggers"] == []


# ---------------------------------------------------------------- DB helpers
def _make_db():
    """Build a minimal SQLite schema sufficient for the actuator
    smoke tests. We keep it scoped to ONLY the columns / tables the
    actuator + reconciler touch."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref TEXT,
            company_name TEXT,
            country TEXT,
            sector TEXT,
            entity_type TEXT,
            ownership_structure TEXT,
            risk_score REAL,
            risk_level TEXT,
            base_risk_level TEXT,
            final_risk_level TEXT,
            onboarding_lane TEXT,
            status TEXT,
            is_fixture INTEGER DEFAULT 0
        );
        CREATE TABLE edd_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER,
            client_name TEXT,
            risk_level TEXT,
            risk_score REAL,
            stage TEXT,
            assigned_officer TEXT,
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
            ip_address TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------- G & H
def test_g_h_actuator_creates_then_idempotent_edd_case(monkeypatch):
    # Stub server._actuate_edd_routing + _emit_edd_routing_audit for
    # this unit test so we don't need to import the giant server
    # module. We assert the actuator wires the call correctly.
    import importlib
    import routing_actuator as ra

    conn = _make_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO applications (ref, company_name, country, sector, "
        "final_risk_level, onboarding_lane, status) VALUES (?,?,?,?,?,?,?)",
        ("ARF-T-1", "PEP Co", "British Virgin Islands",
         "Crypto / Digital Assets Exchange", "MEDIUM",
         "Standard Review", "in_review"),
    )
    conn.commit()
    app_row = dict(cur.execute(
        "SELECT * FROM applications WHERE ref=?", ("ARF-T-1",)
    ).fetchone())

    # Synthetic shims for the two server-internal helpers
    actuate_calls = []

    def fake_actuate(db, app_row, edd_routing, sup_result, user, ip):
        actuate_calls.append({"app_ref": app_row.get("ref"),
                              "route": edd_routing.get("route")})
        # Simulate idempotent insert: only one row per application
        existing = db.execute(
            "SELECT id FROM edd_cases WHERE application_id=? "
            "AND stage NOT IN ('edd_approved','edd_rejected')",
            (app_row.get("id"),)
        ).fetchone()
        if existing:
            return {"case_id": existing["id"], "created": False,
                    "status_changed": False, "skipped": False}
        cur = db.execute(
            "INSERT INTO edd_cases (application_id, client_name, "
            "risk_level, risk_score, stage, assigned_officer, "
            "trigger_source, trigger_notes, edd_notes) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (app_row["id"], app_row.get("company_name"),
             app_row.get("final_risk_level") or "HIGH", 0.0,
             "triggered", None, "policy_routing",
             "; ".join(edd_routing.get("triggers") or []), "[]"),
        )
        return {"case_id": cur.lastrowid, "created": True,
                "status_changed": True, "skipped": False}

    audit_calls = []

    def fake_emit(db, user, app_ref, routing, ip):
        audit_calls.append((app_ref, routing.get("policy_version"),
                            list(routing.get("triggers") or [])))
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, "
            "action, target, detail, ip_address) "
            "VALUES (?,?,?,?,?,?,?)",
            ("system", "system", "system",
             "edd_routing.evaluated",
             f"application:{app_ref}",
             json.dumps({
                 "policy_version": routing.get("policy_version"),
                 "route": routing.get("route"),
                 "triggers": list(routing.get("triggers") or []),
                 "inputs": routing.get("inputs"),
             }), ""),
        )

    server_stub = type("S", (), {})()
    server_stub._actuate_edd_routing = fake_actuate
    server_stub._emit_edd_routing_audit = fake_emit
    monkeypatch.setitem(sys.modules, "server", server_stub)

    # First evaluation -> creates a case
    out1 = ra.apply_routing_decision(
        db=conn, app_row=app_row, source=ra.SOURCE_PRESCREENING_SUBMIT,
        user={"sub": "u", "name": "u", "role": "admin"},
    )
    assert out1["ran"] is True
    assert out1["route"] == "edd"
    assert out1["lane_persisted"] == "EDD"

    n1 = conn.execute("SELECT COUNT(*) c FROM edd_cases").fetchone()["c"]
    assert n1 == 1

    # Second evaluation with identical facts -> NO duplicate
    out2 = ra.apply_routing_decision(
        db=conn, app_row=app_row, source=ra.SOURCE_RISK_RECOMPUTE,
        user={"sub": "u", "name": "u", "role": "admin"},
    )
    assert out2["route"] == "edd"
    n2 = conn.execute("SELECT COUNT(*) c FROM edd_cases").fetchone()["c"]
    assert n2 == 1, "actuator must be idempotent"

    # H subcase: lane is now persisted as EDD
    lane_row = conn.execute(
        "SELECT onboarding_lane FROM applications WHERE id=?",
        (app_row["id"],)
    ).fetchone()
    assert lane_row["onboarding_lane"] == "EDD"


# ---------------------------------------------------------------- I
def test_i_audit_row_contains_policy_version_inputs_triggers(monkeypatch):
    import routing_actuator as ra

    conn = _make_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO applications (ref, company_name, country, sector, "
        "final_risk_level, onboarding_lane, status) VALUES (?,?,?,?,?,?,?)",
        ("ARF-T-2", "Std SME", "Mauritius", "Consulting",
         "MEDIUM", "Standard Review", "in_review"),
    )
    conn.commit()
    app_row = dict(cur.execute(
        "SELECT * FROM applications WHERE ref=?", ("ARF-T-2",)
    ).fetchone())

    server_stub = type("S", (), {})()
    server_stub._actuate_edd_routing = lambda *a, **k: {
        "case_id": None, "created": False, "status_changed": False,
        "skipped": True
    }

    def fake_emit(db, user, app_ref, routing, ip):
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, "
            "action, target, detail, ip_address) "
            "VALUES (?,?,?,?,?,?,?)",
            ("system", "system", "system",
             "edd_routing.evaluated",
             f"application:{app_ref}",
             json.dumps({
                 "policy_version": routing.get("policy_version"),
                 "route": routing.get("route"),
                 "triggers": list(routing.get("triggers") or []),
                 "inputs": routing.get("inputs"),
             }), ""),
        )

    server_stub._emit_edd_routing_audit = fake_emit
    monkeypatch.setitem(sys.modules, "server", server_stub)

    ra.apply_routing_decision(
        db=conn, app_row=app_row, source=ra.SOURCE_PRESCREENING_SUBMIT,
    )

    audit = conn.execute(
        "SELECT * FROM audit_log WHERE action='edd_routing.evaluated'"
    ).fetchone()
    assert audit is not None
    detail = json.loads(audit["detail"])
    assert detail["policy_version"] == "edd_routing_policy_v1"
    assert "route" in detail
    assert "triggers" in detail
    assert "inputs" in detail


# ---------------------------------------------------------------- J & K
def test_j_k_reconciliation_detects_drift_and_skips_fixtures(monkeypatch):
    """Drift detection: a non-fixture EDD-policy-trigger app whose
    lane is Standard should be flagged. A fixture app with the same
    facts must NOT be flagged when fixtures are excluded by default.
    """
    import importlib
    import sys as _sys

    # Make sure the reconciler path is importable
    here = os.path.dirname(os.path.abspath(__file__))
    backend = os.path.dirname(here)
    tools_dir = os.path.join(backend, "tools")
    if tools_dir not in _sys.path:
        _sys.path.insert(0, tools_dir)

    conn = _make_db()
    cur = conn.cursor()
    # Live crypto+BVI MEDIUM case (drift expected: lane != EDD)
    cur.execute(
        "INSERT INTO applications (ref, company_name, country, sector, "
        "final_risk_level, onboarding_lane, status, is_fixture) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("ARF-LIVE-1", "Coin Co", "British Virgin Islands",
         "Crypto / Digital Assets Exchange", "MEDIUM",
         "Standard Review", "in_review", 0),
    )
    # Fixture row with the same facts -- must be ignored by default
    cur.execute(
        "INSERT INTO applications (ref, company_name, country, sector, "
        "final_risk_level, onboarding_lane, status, is_fixture) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("ARF-FIX-1", "Fixture Co", "British Virgin Islands",
         "Crypto / Digital Assets Exchange", "MEDIUM",
         "Standard Review", "in_review", 1),
    )
    conn.commit()

    import reconcile_edd_routing as rec
    findings = rec._scan(conn)
    refs = {f["ref"] for f in findings}
    assert "ARF-LIVE-1" in refs, "live drift must be detected"
    assert "ARF-FIX-1" not in refs, "fixture must be excluded by default"

    # At least one finding for the live case must be lane_drift_to_edd
    kinds = {(f["ref"], f["kind"]) for f in findings}
    assert ("ARF-LIVE-1", "lane_drift_to_edd") in kinds


def test_l_reconciler_sql_uses_dialect_neutral_bool_filter():
    """Reconciler must not use COALESCE(is_fixture, 0) - PG bool can't COALESCE with int 0."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "tools" / "reconcile_edd_routing.py"
    text = src.read_text(encoding="utf-8")
    assert "COALESCE(is_fixture, 0)" not in text, (
        "Reconciler must use dialect-neutral fixture filter; "
        "COALESCE(boolean, 0) raises DatatypeMismatch on PostgreSQL."
    )
    assert "is_fixture IS NOT TRUE" in text, (
        "Reconciler should filter with `is_fixture IS NOT TRUE` "
        "(works for PG BOOLEAN and SQLite INTEGER)."
    )
