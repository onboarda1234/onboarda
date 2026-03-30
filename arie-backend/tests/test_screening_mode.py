import json


def _live_report():
    return {
        "company_screening": {
            "found": True,
            "source": "opencorporates",
            "api_status": "live",
            "sanctions": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
        },
        "director_screenings": [
            {
                "person_name": "John Smith",
                "screening": {"matched": False, "results": [], "source": "sumsub", "api_status": "live"},
            }
        ],
        "ubo_screenings": [],
        "ip_geolocation": {"source": "ipapi", "api_status": "live", "risk_level": "LOW"},
        "kyc_applicants": [{"person_name": "John Smith", "source": "sumsub", "api_status": "live", "review_answer": "GREEN"}],
    }


def test_determine_screening_mode_live_for_actual_report_shape(temp_db):
    from security_hardening import determine_screening_mode

    assert determine_screening_mode(_live_report()) == "live"


def test_determine_screening_mode_simulated_when_nested_provider_is_simulated(temp_db):
    from security_hardening import determine_screening_mode

    report = _live_report()
    report["director_screenings"][0]["screening"]["api_status"] = "simulated"
    report["director_screenings"][0]["screening"]["source"] = "simulated"

    assert determine_screening_mode(report) == "simulated"


def test_store_screening_mode_updates_application_column(db, temp_db):
    from security_hardening import store_screening_mode

    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, prescreening_data, screening_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("app_mode", "ARF-MODE", "client_mode", "Mode Co", "Mauritius", "Technology", "SME", "pricing_review", json.dumps({}), "unknown"),
    )
    db.commit()

    assert store_screening_mode(db, "app_mode", "live") is True
    db.commit()

    row = db.execute("SELECT screening_mode FROM applications WHERE id=?", ("app_mode",)).fetchone()
    assert row["screening_mode"] == "live"


def test_approval_gate_rejects_simulated_nested_screening_report(db, temp_db):
    from security_hardening import ApprovalGateValidator

    app_id = "app_gate_nested"
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            "ARF-GATE-NESTED",
            "client_nested",
            "Nested Gate Co",
            "Mauritius",
            "Technology",
            "SME",
            "compliance_review",
            "MEDIUM",
            45,
            json.dumps({"screening_report": dict(_live_report(), screening_mode="live")}),
        ),
    )
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation, review_status, quality_score, validation_status, supervisor_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (app_id, json.dumps({"ai_source": "deterministic"}), "system", "APPROVE", "approved", 8.5, "pass", "CONSISTENT"),
    )
    db.commit()

    # First confirm live nested report is acceptable to the screening-status checks.
    app = dict(db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True
    assert message == ""

    simulated_report = _live_report()
    simulated_report["director_screenings"][0]["screening"]["api_status"] = "simulated"
    simulated_report["director_screenings"][0]["screening"]["source"] = "simulated"
    simulated_report["screening_mode"] = "simulated"
    db.execute("UPDATE applications SET prescreening_data=? WHERE id=?", (json.dumps({"screening_report": simulated_report}), app_id))
    db.commit()

    app = dict(db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "simulated" in message.lower()
