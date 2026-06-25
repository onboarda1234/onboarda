import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _base_app(**overrides):
    app = {
        "entity_type": "SME / Private Company",
        "ownership_structure": "Simple - direct identifiable UBOs",
        "country": "Mauritius",
        "sector": "Software / SaaS",
        "monthly_volume": "0-50000",
        "source_of_funds": "Operating revenue",
        "directors": [],
        "ubos": [{"full_name": "Clean Owner", "is_pep": "No", "ownership_pct": 100}],
    }
    app.update(overrides)
    return app


def test_provider_only_pep_flag_does_not_score_as_declared_pep(temp_db):
    from rule_engine import compute_risk_score

    clean = compute_risk_score(_base_app())
    provider_only = compute_risk_score(
        _base_app(
            directors=[
                {
                    "full_name": "Provider Match",
                    "is_pep": "Yes",
                    "pep_declaration": {
                        "declared_pep": False,
                        "client_declared_pep": False,
                        "pep_status": "declared_no",
                    },
                }
            ]
        )
    )

    assert provider_only["declared_pep_present"] is False
    assert provider_only["dimensions"]["d1"] == clean["dimensions"]["d1"]
    assert "floor_rule_declared_pep" not in provider_only["escalations"]


def test_officer_confirmed_pep_still_scores_and_routes(temp_db):
    from rule_engine import compute_risk_score

    result = compute_risk_score(
        _base_app(
            directors=[
                {
                    "full_name": "Confirmed Match",
                    "is_pep": "Yes",
                    "pep_type": "foreign_pep",
                    "pep_declaration": {
                        "declared_pep": False,
                        "client_declared_pep": False,
                        "officer_verified_pep": True,
                        "pep_status": "confirmed_pep",
                    },
                }
            ]
        )
    )

    assert result["declared_pep_present"] is True
    assert result["final_risk_level"] in {"HIGH", "VERY_HIGH"}
    assert result["lane"] == "EDD"
    assert "floor_rule_declared_pep" in result["escalations"]


def test_false_positive_pep_does_not_score_as_declared_pep(temp_db):
    from rule_engine import compute_risk_score

    result = compute_risk_score(
        _base_app(
            directors=[
                {
                    "full_name": "Cleared Match",
                    "is_pep": "Yes",
                    "pep_declaration": {
                        "declared_pep": False,
                        "client_declared_pep": False,
                        "officer_verified_pep": False,
                        "pep_status": "false_positive",
                    },
                }
            ]
        )
    )

    assert result["declared_pep_present"] is False
    assert "floor_rule_declared_pep" not in result["escalations"]


def test_provider_detection_repair_resets_only_matching_stale_party_flags(temp_db):
    from db import (
        _PEP_PROVIDER_DETECTION_REPAIR_MARKER_KEY,
        _repair_provider_detected_pep_party_flags_once,
        get_db,
    )

    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM data_migration_markers WHERE marker_key=?",
            (_PEP_PROVIDER_DETECTION_REPAIR_MARKER_KEY,),
        )
        conn.execute(
            """
            INSERT INTO applications (id, ref, client_id, company_name, country, status, prescreening_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pep-repair-app",
                "PEP-REPAIR",
                "pep-repair-client",
                "PEP Repair Ltd",
                "Mauritius",
                "compliance_review",
                json.dumps(
                    {
                        "screening_report": {
                            "director_screenings": [
                                {
                                    "source_id": "pep-repair-provider",
                                    "undeclared_pep": True,
                                    "provider_detected_pep": True,
                                    "has_pep_hit": True,
                                }
                            ],
                            "ubo_screenings": [],
                        }
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO directors (id, application_id, full_name, is_pep, pep_declaration)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "pep-repair-provider",
                "pep-repair-app",
                "Provider Match",
                "Yes",
                json.dumps({"declared_pep": False, "client_declared_pep": False, "pep_status": "declared_no"}),
            ),
        )
        conn.execute(
            """
            INSERT INTO directors (id, application_id, full_name, is_pep, pep_declaration)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "pep-repair-confirmed",
                "pep-repair-app",
                "Confirmed PEP",
                "Yes",
                json.dumps({"officer_verified_pep": True, "pep_status": "confirmed_pep"}),
            ),
        )

        assert _repair_provider_detected_pep_party_flags_once(conn) is True
        conn.commit()

        repaired = conn.execute(
            "SELECT is_pep, pep_declaration FROM directors WHERE id=?",
            ("pep-repair-provider",),
        ).fetchone()
        confirmed = conn.execute(
            "SELECT is_pep FROM directors WHERE id=?",
            ("pep-repair-confirmed",),
        ).fetchone()
        marker = conn.execute(
            "SELECT description FROM data_migration_markers WHERE marker_key=?",
            (_PEP_PROVIDER_DETECTION_REPAIR_MARKER_KEY,),
        ).fetchone()

        assert repaired["is_pep"] == "No"
        assert json.loads(repaired["pep_declaration"])["provider_detection_repair"]["source"] == (
            "PR-PEP-PROVIDER-DETECTION-SEPARATION-1"
        )
        assert confirmed["is_pep"] == "Yes"
        assert "rows_repaired=1" in marker["description"]
    finally:
        conn.close()


def test_declared_pep_risk_escalation_text_does_not_become_provider_pep_basis(temp_db):
    from security_hardening import classify_approval_route

    route = classify_approval_route(
        {
            "id": "pep-route-app",
            "status": "compliance_review",
            "risk_level": "MEDIUM",
            "final_risk_level": "MEDIUM",
            "risk_escalations": json.dumps(["floor_rule_declared_pep"]),
            "prescreening_data": "{}",
        }
    )

    assert "declared_pep_present" in route["escalation_reasons"]
    assert "provider_pep_match_unresolved" not in route["escalation_reasons"]
    assert "pep" not in route["escalation_reasons"]


def test_submit_prescreening_no_longer_writes_provider_pep_to_party_state():
    server_source = (REPO_ROOT / "arie-backend" / "server.py").read_text(encoding="utf-8")

    assert "UPDATE directors SET is_pep='Yes'" not in server_source
    assert "UPDATE ubos SET is_pep='Yes'" not in server_source
    assert "Provider PEP detections are screening evidence" in server_source


def test_backoffice_declared_pep_count_is_separate_from_provider_pep_count():
    html = (REPO_ROOT / "arie-backoffice.html").read_text(encoding="utf-8")

    assert "screeningPepCount" in html
    assert "Provider PEP Matches" in html
    assert "return screeningPeps;" not in html
    assert "var declared = directors.filter(personHasDeclaredOrVerifiedPep)" not in html
