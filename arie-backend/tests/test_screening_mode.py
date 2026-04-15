import json
from datetime import datetime, timedelta, timezone


def _live_report():
    now = datetime.now(timezone.utc)
    return {
        "screened_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
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
    now = datetime.now(timezone.utc)
    screened_at = now.strftime("%Y-%m-%dT%H:%M:%S")
    valid_until = (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
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
            json.dumps({
                "screening_report": dict(_live_report(), screening_mode="live"),
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            }),
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


# ═══════════════════════════════════════════════════════════════
# EX-06: Gate 5 enrichment vs required screening policy
# ═══════════════════════════════════════════════════════════════

def _insert_gate5_app(db, app_id, screening_report):
    """Helper: insert application + valid compliance memo for Gate 5 testing."""
    now = datetime.now(timezone.utc)
    valid_until = (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type, status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            f"ARF-G5-{app_id[-6:].upper()}",
            f"client_{app_id}",
            "Gate5 Test Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "compliance_review",
            "MEDIUM",
            45,
            json.dumps({
                "screening_report": dict(screening_report, screening_mode="live"),
                "screening_valid_until": valid_until,
                "screening_validity_days": 90,
            }),
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
    return dict(db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())


def test_gate5_allows_simulated_company_registry_with_live_sumsub(db, temp_db):
    """Gate 5 must NOT block when company_registry (OpenCorporates) is simulated
    but all required Sumsub checks are live."""
    from security_hardening import ApprovalGateValidator

    report = _live_report()
    report["company_screening"]["source"] = "simulated"
    report["company_screening"]["api_status"] = "simulated"
    # Sumsub sanctions sub-check stays live
    assert report["company_screening"]["sanctions"]["api_status"] == "live"

    app = _insert_gate5_app(db, "app_g5_cr_sim", report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval but got: {message}"
    assert message == ""


def test_gate5_allows_simulated_ip_geolocation_with_live_sumsub(db, temp_db):
    """Gate 5 must NOT block when ip_geolocation is simulated
    but all required Sumsub checks are live."""
    from security_hardening import ApprovalGateValidator

    report = _live_report()
    report["ip_geolocation"]["source"] = "simulated"
    report["ip_geolocation"]["api_status"] = "simulated"

    app = _insert_gate5_app(db, "app_g5_ip_sim", report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval but got: {message}"
    assert message == ""


def test_gate5_blocks_simulated_company_watchlist(db, temp_db):
    """Gate 5 MUST block when company_watchlist (Sumsub company sanctions) is simulated."""
    from security_hardening import ApprovalGateValidator

    report = _live_report()
    report["company_screening"]["sanctions"]["source"] = "simulated"
    report["company_screening"]["sanctions"]["api_status"] = "simulated"

    app = _insert_gate5_app(db, "app_g5_wl_sim", report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "company_watchlist" in message
    assert "simulated" in message.lower()


def test_gate5_blocks_simulated_director_screening(db, temp_db):
    """Gate 5 MUST block when director AML/PEP screening (Sumsub) is simulated."""
    from security_hardening import ApprovalGateValidator

    report = _live_report()
    report["director_screenings"][0]["screening"]["source"] = "simulated"
    report["director_screenings"][0]["screening"]["api_status"] = "simulated"

    app = _insert_gate5_app(db, "app_g5_dir_sim", report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "director_screening" in message
    assert "simulated" in message.lower()


def test_gate5_blocks_simulated_ubo_screening(db, temp_db):
    """Gate 5 MUST block when UBO AML/PEP screening (Sumsub) is simulated."""
    from security_hardening import ApprovalGateValidator

    report = _live_report()
    report["ubo_screenings"] = [
        {
            "person_name": "Jane Doe",
            "screening": {"matched": False, "results": [], "source": "simulated", "api_status": "simulated"},
        }
    ]

    app = _insert_gate5_app(db, "app_g5_ubo_sim", report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "ubo_screening" in message
    assert "simulated" in message.lower()


def test_gate5_blocks_simulated_kyc_applicant(db, temp_db):
    """Gate 5 MUST block when KYC applicant check (Sumsub) is simulated."""
    from security_hardening import ApprovalGateValidator

    report = _live_report()
    report["kyc_applicants"][0]["source"] = "simulated"
    report["kyc_applicants"][0]["api_status"] = "simulated"

    app = _insert_gate5_app(db, "app_g5_kyc_sim", report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "kyc_applicant" in message
    assert "simulated" in message.lower()


def test_determine_screening_mode_live_when_only_enrichment_simulated(temp_db):
    """determine_screening_mode() must return 'live' when only enrichment
    sources (company_registry, ip_geolocation) are simulated but all
    required Sumsub checks are live."""
    from security_hardening import determine_screening_mode

    report = _live_report()
    report["company_screening"]["source"] = "simulated"
    report["company_screening"]["api_status"] = "simulated"
    report["ip_geolocation"]["source"] = "simulated"
    report["ip_geolocation"]["api_status"] = "simulated"

    assert determine_screening_mode(report) == "live"


def test_determine_screening_mode_simulated_when_required_source_simulated(temp_db):
    """determine_screening_mode() must return 'simulated' when any required
    Sumsub source is simulated, even if enrichment is live."""
    from security_hardening import determine_screening_mode

    report = _live_report()
    report["kyc_applicants"][0]["source"] = "simulated"
    report["kyc_applicants"][0]["api_status"] = "simulated"

    assert determine_screening_mode(report) == "simulated"


def test_evidence_includes_is_required_field(temp_db):
    """_collect_screening_provider_evidence must include is_required flag
    distinguishing required Sumsub checks from enrichment."""
    from security_hardening import _collect_screening_provider_evidence

    report = _live_report()
    evidence = _collect_screening_provider_evidence(report)

    by_name = {e["name"]: e for e in evidence}

    # Enrichment — is_required must be False
    assert by_name["company_registry"]["is_required"] is False
    assert by_name["ip_geolocation"]["is_required"] is False

    # Required — is_required must be True
    assert by_name["company_watchlist"]["is_required"] is True
    assert by_name["director_screening_0"]["is_required"] is True
    assert by_name["kyc_applicant_0"]["is_required"] is True


def test_gate5_allows_both_enrichment_simulated_simultaneously(db, temp_db):
    """Gate 5 must allow approval when BOTH company_registry AND ip_geolocation
    are simulated but all required Sumsub checks are live."""
    from security_hardening import ApprovalGateValidator

    report = _live_report()
    report["company_screening"]["source"] = "simulated"
    report["company_screening"]["api_status"] = "simulated"
    report["ip_geolocation"]["source"] = "simulated"
    report["ip_geolocation"]["api_status"] = "simulated"

    app = _insert_gate5_app(db, "app_g5_both_e", report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval but got: {message}"
    assert message == ""
