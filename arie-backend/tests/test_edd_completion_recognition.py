import json
import os
import sqlite3
import sys
from datetime import datetime, timezone


BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests.conftest import make_base_memo


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref TEXT,
            company_name TEXT,
            risk_level TEXT,
            final_risk_level TEXT,
            risk_score REAL,
            onboarding_lane TEXT,
            status TEXT,
            pre_approval_decision TEXT,
            updated_at TEXT
        );
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            name TEXT,
            role TEXT
        );
        CREATE TABLE edd_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL,
            client_name TEXT NOT NULL,
            risk_level TEXT,
            risk_score REAL,
            stage TEXT,
            assigned_officer TEXT,
            senior_reviewer TEXT,
            trigger_source TEXT,
            trigger_notes TEXT,
            origin_context TEXT,
            closed_at TEXT,
            sla_due_at TEXT,
            priority TEXT,
            edd_notes TEXT DEFAULT '[]',
            decision TEXT,
            decision_reason TEXT,
            decided_by TEXT,
            decided_at TEXT,
            triggered_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE edd_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            edd_case_id INTEGER NOT NULL UNIQUE,
            findings_summary TEXT,
            key_concerns TEXT DEFAULT '[]',
            mitigating_evidence TEXT DEFAULT '[]',
            recommended_outcome TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE application_enhanced_requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL,
            trigger_key TEXT,
            requirement_key TEXT,
            status TEXT,
            mandatory INTEGER DEFAULT 1,
            blocking_approval INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute("INSERT INTO users (id, name, role) VALUES ('sco1', 'Senior Officer', 'sco')")
    conn.commit()
    return conn


def _insert_app(conn, *, ref="ARF-EDD-COMP", status="kyc_submitted"):
    conn.execute(
        """
        INSERT INTO applications
            (ref, company_name, risk_level, final_risk_level, risk_score, onboarding_lane, status, pre_approval_decision)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ref, "EDD Complete Ltd", "HIGH", "HIGH", 72, "EDD", status, "PRE_APPROVE"),
    )
    app_id = conn.execute("SELECT id FROM applications WHERE ref=?", (ref,)).fetchone()["id"]
    conn.commit()
    return app_id


def _insert_resolved_requirements(conn, app_id, *, count=2):
    for index in range(count):
        conn.execute(
            """
            INSERT INTO application_enhanced_requirements
                (application_id, trigger_key, requirement_key, status, mandatory, blocking_approval, active)
            VALUES (?, ?, ?, 'accepted', 1, 1, 1)
            """,
            (app_id, "pep", f"req_{index}"),
        )
    conn.commit()


def _insert_approved_edd(conn, app_id, *, triggers, stage="edd_approved", findings=True, audit=True):
    now = datetime.now(timezone.utc).isoformat()
    notes = json.dumps(
        [
            {
                "ts": now,
                "author": "Senior Officer",
                "source": "policy_routing",
                "policy_version": "edd_routing_policy_v1",
                "triggers": triggers,
                "note": "EDD case auto-created by routing policy actuation",
            }
        ]
    )
    conn.execute(
        """
        INSERT INTO edd_cases
            (application_id, client_name, risk_level, risk_score, stage, assigned_officer,
             senior_reviewer, trigger_source, trigger_notes, edd_notes, decision,
             decision_reason, decided_by, decided_at)
        VALUES (?, 'EDD Complete Ltd', 'HIGH', 72, ?, 'co1', 'sco1',
                'policy_routing', ?, ?, ?, 'EDD approved after senior review', 'sco1', ?)
        """,
        (
            app_id,
            stage,
            "Auto-routed to EDD by policy edd_routing_policy_v1 | triggers: " + ", ".join(triggers),
            notes,
            stage,
            now,
        ),
    )
    case_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    if findings:
        conn.execute(
            """
            INSERT INTO edd_findings
                (edd_case_id, findings_summary, key_concerns, mitigating_evidence, recommended_outcome)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                case_id,
                "Structured EDD findings confirm residual risk is acceptable after senior review.",
                json.dumps(["Declared PEP exposure reviewed"]),
                json.dumps(["Senior approval and enhanced evidence accepted"]),
                "approve",
            ),
        )
    if audit:
        app_ref = conn.execute("SELECT ref FROM applications WHERE id=?", (app_id,)).fetchone()["ref"]
        conn.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) "
            "VALUES ('sco1', 'Senior Officer', 'sco', 'EDD Closure (dual-control)', ?, ?, '')",
            (
                app_ref,
                json.dumps({"edd_case_id": case_id, "decision": stage, "closed_by": "sco1"}),
            ),
        )
    conn.commit()
    return case_id


def _edd_routing(*triggers):
    return {
        "policy_version": "edd_routing_policy_v1",
        "route": "edd",
        "triggers": list(triggers),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


def _supervisor_memo(*, trigger="declared_pep_present", completion=None):
    contract = {
        "final_risk_level": "HIGH",
        "declared_pep_present": trigger == "declared_pep_present",
        "risk_dimensions": {
            "jurisdiction": "LOW",
            "business": "HIGH" if trigger == "crypto_or_virtual_asset_sector" else "LOW",
        },
        "ownership_transparency_status": "clear",
        "screening_terminality_summary": {"terminal": True, "approval_blocking": False},
    }
    if completion:
        contract["edd_completion"] = completion
    return make_base_memo(
        {
            "sections": {
                "executive_summary": {"content": "High-risk case with documented EDD evidence."},
                "risk_assessment": {
                    "content": "Overall risk: HIGH after EDD-triggering factor.",
                    "sub_sections": {
                        "jurisdiction_risk": {"rating": "LOW", "content": "Mauritius"},
                        "business_risk": {"rating": "HIGH" if trigger == "crypto_or_virtual_asset_sector" else "LOW", "content": "Sector reviewed"},
                        "transaction_risk": {"rating": "MEDIUM", "content": "Standard expected activity"},
                        "ownership_risk": {"rating": "LOW", "content": "Clear ownership"},
                        "financial_crime_risk": {"rating": "HIGH", "content": "EDD-triggering factor reviewed"},
                    },
                },
                "compliance_decision": {
                    "decision": "APPROVE_WITH_CONDITIONS",
                    "content": "Approve with conditions based on completed EDD evidence.",
                },
            },
            "metadata": {
                "risk_rating": "HIGH",
                "approval_recommendation": "APPROVE_WITH_CONDITIONS",
                "agent5_input_contract": contract,
                "edd_completion": completion or {},
            },
        }
    )


def test_pep_completed_edd_resolves_supervisor_mandatory_escalation():
    from supervisor_engine import run_memo_supervisor

    completion = {
        "satisfied": True,
        "covers_current_triggers": True,
        "case_id": 204,
        "current_triggers": ["declared_pep_present"],
    }
    result = run_memo_supervisor(
        _supervisor_memo(trigger="declared_pep_present", completion=completion)
    )

    assert result["verdict"] == "CONSISTENT"
    assert result["mandatory_escalation"] is False
    assert result["can_approve"] is True
    assert "declared_pep_present" in result["mandatory_escalation_resolved_by_edd"]


def test_crypto_completed_edd_resolves_supervisor_mandatory_escalation():
    from supervisor_engine import run_memo_supervisor

    completion = {
        "satisfied": True,
        "covers_current_triggers": True,
        "case_id": 206,
        "current_triggers": ["crypto_or_virtual_asset_sector"],
    }
    result = run_memo_supervisor(
        _supervisor_memo(trigger="crypto_or_virtual_asset_sector", completion=completion)
    )

    assert result["verdict"] == "CONSISTENT"
    assert result["mandatory_escalation"] is False
    assert result["can_approve"] is True
    assert "sector_risk_tier=HIGH" in result["mandatory_escalation_resolved_by_edd"]


def test_high_risk_without_completed_edd_still_blocks_supervisor():
    from supervisor_engine import run_memo_supervisor

    result = run_memo_supervisor(_supervisor_memo(trigger="declared_pep_present"))

    assert result["verdict"] == "CONSISTENT"
    assert result["mandatory_escalation"] is True
    assert result["can_approve"] is False
    assert "final_risk_level=HIGH" in result["mandatory_escalation_reasons"]
    assert "declared_pep_present" in result["mandatory_escalation_reasons"]


def test_approved_edd_prevents_duplicate_active_case_on_rerun():
    from edd_actuation import actuate_edd_routing
    from edd_completion import collect_edd_completion_status

    conn = _make_db()
    app_id = _insert_app(conn)
    _insert_resolved_requirements(conn, app_id)
    approved_case_id = _insert_approved_edd(conn, app_id, triggers=["declared_pep_present"])
    app = dict(conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    routing = _edd_routing("high_or_very_high_risk", "declared_pep_present")

    completion = collect_edd_completion_status(conn, app_id, routing=routing)
    result = actuate_edd_routing(
        conn,
        app,
        routing,
        {"mandatory_escalation_reasons": ["declared_pep_present"]},
        {"sub": "sco1", "name": "Senior Officer", "role": "sco"},
    )

    assert completion["satisfied"] is True
    assert result["completion_recognized"] is True
    assert result["case_id"] == approved_case_id
    assert result["created"] is False
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id=? AND stage NOT IN ('edd_approved','edd_rejected')",
        (app_id,),
    ).fetchone()["c"] == 0
    audit = conn.execute(
        "SELECT detail FROM audit_log WHERE action='edd_routing.actuated' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    detail = json.loads(audit["detail"])
    assert detail["edd_completion_satisfied"] is True
    assert detail["completion_recognized"] is True


def test_approved_edd_without_findings_still_creates_active_case():
    from edd_actuation import actuate_edd_routing
    from edd_completion import collect_edd_completion_status

    conn = _make_db()
    app_id = _insert_app(conn)
    _insert_resolved_requirements(conn, app_id)
    _insert_approved_edd(conn, app_id, triggers=["declared_pep_present"], findings=False)
    app = dict(conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    routing = _edd_routing("declared_pep_present")

    completion = collect_edd_completion_status(conn, app_id, routing=routing)
    result = actuate_edd_routing(conn, app, routing, {}, {"sub": "sco1", "name": "Senior Officer", "role": "sco"})

    assert completion["satisfied"] is False
    assert "findings_incomplete" in completion["reason"]
    assert result["created"] is True
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id=? AND stage NOT IN ('edd_approved','edd_rejected')",
        (app_id,),
    ).fetchone()["c"] == 1


def test_new_material_trigger_after_edd_approval_requires_new_active_case():
    from edd_actuation import actuate_edd_routing
    from edd_completion import collect_edd_completion_status

    conn = _make_db()
    app_id = _insert_app(conn)
    _insert_resolved_requirements(conn, app_id)
    _insert_approved_edd(conn, app_id, triggers=["declared_pep_present"])
    app = dict(conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    routing = _edd_routing("declared_pep_present", "material_screening_concern")

    completion = collect_edd_completion_status(conn, app_id, routing=routing)
    result = actuate_edd_routing(conn, app, routing, {}, {"sub": "sco1", "name": "Senior Officer", "role": "sco"})

    assert completion["satisfied"] is False
    assert "trigger_coverage_missing" in completion["reason"]
    assert result["created"] is True
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM edd_cases WHERE application_id=? AND stage NOT IN ('edd_approved','edd_rejected')",
        (app_id,),
    ).fetchone()["c"] == 1
