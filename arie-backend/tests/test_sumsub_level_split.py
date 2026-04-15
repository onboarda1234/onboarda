"""
Tests for Sumsub screening config split (EX-06 fix).

Covers:
  1. Individual screening uses SUMSUB_INDIVIDUAL_LEVEL_NAME
  2. SUMSUB_LEVEL_NAME remains backward-compatible fallback for individual screening
  3. Company screening with no SUMSUB_COMPANY_LEVEL_NAME returns not_configured
  4. Company screening with configured company level sends company payload
  5. Gate 5 allows company_watchlist not_configured
  6. Gate 5 blocks company_watchlist error when company level is configured and fails
  7. Gate 5 blocks director/UBO individual screening error
  8. EX-06-style case: approval-gate reachable when individual screening is
     live and company KYB is not_configured
  9. determine_screening_mode treats not_configured as acceptable
"""
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

# Ensure DB_PATH is set before any production-module import triggers config.py.
# The conftest temp_db fixture does this too, but it only runs when a test
# requests the fixture — by then, config.DB_PATH may already be frozen.
if "DB_PATH" not in os.environ:
    os.environ["DB_PATH"] = os.path.join(
        tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db"
    )


# -- 1. environment.py config resolution --


def test_individual_level_explicit(monkeypatch):
    monkeypatch.setenv("SUMSUB_INDIVIDUAL_LEVEL_NAME", "id-only")
    monkeypatch.setenv("SUMSUB_LEVEL_NAME", "basic-kyc-level")
    from environment import get_sumsub_individual_level_name
    assert get_sumsub_individual_level_name() == "id-only"


def test_individual_level_fallback_to_sumsub_level_name(monkeypatch):
    monkeypatch.delenv("SUMSUB_INDIVIDUAL_LEVEL_NAME", raising=False)
    monkeypatch.setenv("SUMSUB_LEVEL_NAME", "basic-kyc-level")
    from environment import get_sumsub_individual_level_name
    assert get_sumsub_individual_level_name() == "basic-kyc-level"


def test_individual_level_default_id_and_liveness(monkeypatch):
    monkeypatch.delenv("SUMSUB_INDIVIDUAL_LEVEL_NAME", raising=False)
    monkeypatch.delenv("SUMSUB_LEVEL_NAME", raising=False)
    from environment import get_sumsub_individual_level_name
    assert get_sumsub_individual_level_name() == "id-and-liveness"


def test_company_level_explicit(monkeypatch):
    monkeypatch.setenv("SUMSUB_COMPANY_LEVEL_NAME", "company-kyb")
    from environment import get_sumsub_company_level_name
    assert get_sumsub_company_level_name() == "company-kyb"


def test_company_level_default_empty(monkeypatch):
    monkeypatch.delenv("SUMSUB_COMPANY_LEVEL_NAME", raising=False)
    from environment import get_sumsub_company_level_name
    assert get_sumsub_company_level_name() == ""


# -- 2. screening.py: screen_sumsub_aml routing --


def test_company_screening_returns_not_configured(monkeypatch):
    monkeypatch.delenv("SUMSUB_COMPANY_LEVEL_NAME", raising=False)
    from screening import screen_sumsub_aml
    result = screen_sumsub_aml("Acme Corp", entity_type="Company")
    assert result["api_status"] == "not_configured"
    assert result["source"] == "sumsub"
    assert result["matched"] is False
    assert "not configured" in result.get("reason", "").lower()
    assert "screened_at" in result


def test_individual_screening_not_affected_by_missing_company_level(monkeypatch):
    """Individual screening must NOT return not_configured."""
    monkeypatch.delenv("SUMSUB_COMPANY_LEVEL_NAME", raising=False)
    monkeypatch.delenv("SUMSUB_INDIVIDUAL_LEVEL_NAME", raising=False)
    monkeypatch.delenv("SUMSUB_LEVEL_NAME", raising=False)
    from screening import screen_sumsub_aml
    result = screen_sumsub_aml("John Doe", entity_type="Person")
    assert result.get("api_status") != "not_configured"


def test_company_screening_does_not_shortcircuit_when_level_set(monkeypatch):
    monkeypatch.setenv("SUMSUB_COMPANY_LEVEL_NAME", "company-kyb")
    from screening import screen_sumsub_aml
    result = screen_sumsub_aml("Acme Corp", entity_type="Company")
    assert result.get("api_status") != "not_configured"


def test_individual_level_passed_to_create_applicant(monkeypatch):
    """Verify individual screening passes the AML level to create_applicant."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")
    monkeypatch.delenv("SUMSUB_COMPANY_LEVEL_NAME", raising=False)

    captured = {}

    def fake_create_applicant(**kwargs):
        captured.update(kwargs)
        return {
            "applicant_id": "fake_id",
            "external_user_id": kwargs.get("external_user_id", ""),
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

    import screening
    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create_applicant
    try:
        from unittest.mock import MagicMock, patch
        mock_client = MagicMock()
        mock_client.request_check.return_value = {
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "fake_id",
            "review_status": "completed",
            "review_answer": "GREEN",
            "source": "sumsub",
            "api_status": "live",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            screening.screen_sumsub_aml("John Doe", entity_type="Person")

        assert captured.get("level_name") == "aml-screening"
    finally:
        screening.sumsub_create_applicant = original


# -- 3. Gate 5: company_watchlist not_configured handling --


def _make_screening_report(*, company_sanctions_status="live", director_status="live",
                           kyc_status="live"):
    """Build a screening report with configurable statuses."""
    return {
        "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "company_screening": {
            "found": True,
            "source": "opencorporates",
            "api_status": "live",
            "sanctions": {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": company_sanctions_status,
                **({"reason": "Sumsub company KYB level not configured"}
                   if company_sanctions_status == "not_configured" else {}),
            },
        },
        "director_screenings": [
            {
                "person_name": "John Smith",
                "screening": {
                    "matched": False,
                    "results": [],
                    "source": "sumsub",
                    "api_status": director_status,
                },
            }
        ],
        "ubo_screenings": [],
        "ip_geolocation": {"source": "ipapi", "api_status": "live", "risk_level": "LOW"},
        "kyc_applicants": [
            {
                "person_name": "John Smith",
                "source": "sumsub",
                "api_status": kyc_status,
                "review_answer": "GREEN",
            }
        ],
    }


def _insert_app_for_gate5(db, screening_report):
    """Insert an application + memo that would pass gates 1-4, configurable screening."""
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-level-split-{suffix}"
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type,
         status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            f"ARF-LS-{suffix}",
            f"client-ls-{suffix}",
            "Level Split Test Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "compliance_review",
            "MEDIUM",
            45,
            json.dumps({
                "screening_report": screening_report,
                "screening_valid_until": (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S"),
                "screening_validity_days": 90,
            }),
        ),
    )
    db.execute(
        """
        INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation,
         review_status, quality_score, validation_status, supervisor_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            json.dumps({"ai_source": "deterministic", "metadata": {"ai_source": "deterministic"}}),
            "system",
            "APPROVE_WITH_CONDITIONS",
            "approved",
            8.5,
            "pass",
            "CONSISTENT",
        ),
    )
    db.commit()
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(app)


def test_gate5_allows_not_configured_company_watchlist(db, temp_db):
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(company_sanctions_status="not_configured")
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval, got: {message}"


def test_gate5_blocks_error_company_watchlist(db, temp_db):
    """When company level IS configured but Sumsub returns error, Gate 5 must block."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(company_sanctions_status="error")
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "company_watchlist" in message


def test_gate5_blocks_simulated_company_watchlist(db, temp_db):
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(company_sanctions_status="simulated")
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "simulated" in message.lower()


def test_gate5_blocks_director_screening_error(db, temp_db):
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="error",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "director_screening" in message


def test_gate5_blocks_director_screening_simulated(db, temp_db):
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="simulated",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "simulated" in message.lower()


def test_gate5_blocks_kyc_applicant_error(db, temp_db):
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        kyc_status="error",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "kyc_applicant" in message


# -- 4. EX-06-style end-to-end scenario --


def test_ex06_approval_gate_reachable(db, temp_db):
    """The exact scenario that was blocked: individual screening works,
    company KYB level doesn't exist -> approval should be reachable."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="live",
        kyc_status="live",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"EX-06 scenario should be approvable, got: {message}"


# -- 5. determine_screening_mode with not_configured --


def test_screening_mode_live_when_company_not_configured(temp_db):
    from security_hardening import determine_screening_mode

    report = _make_screening_report(company_sanctions_status="not_configured")
    assert determine_screening_mode(report) == "live"


def test_screening_mode_simulated_when_director_simulated(temp_db):
    from security_hardening import determine_screening_mode

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="simulated",
    )
    assert determine_screening_mode(report) == "simulated"


def test_screening_mode_unknown_when_director_error(temp_db):
    from security_hardening import determine_screening_mode

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="error",
    )
    assert determine_screening_mode(report) == "unknown"
