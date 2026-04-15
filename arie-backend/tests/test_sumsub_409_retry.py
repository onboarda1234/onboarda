"""
Tests for PR #96: Sumsub AML screening retry-safe 409 handling.

Covers:
  1. request_check 409 → /one pending → api_status=pending
  2. request_check 409 → /one GREEN → api_status=live, matched=false
  3. request_check 409 → /one RED → api_status=live, matched=true
  4. request_check 409 → /one unusable → api_status=error
  5. Non-2xx Sumsub responses logged with endpoint/status/body, no secrets
  6. Gate 5 blocks pending person AML
  7. Gate 5 blocks error person AML
  8. Gate 5 allows live GREEN person AML
  9. Company KYB not_configured remains allowed
  10. End-to-end: 409 in screening flow → correct final result
"""
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

# Ensure DB_PATH is set before production-module import triggers config.py.
if "DB_PATH" not in os.environ:
    os.environ["DB_PATH"] = os.path.join(
        tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db"
    )

from unittest.mock import MagicMock, patch


# ── 1. request_check 409 → /one pending → api_status=pending ──


def test_request_check_409_pending():
    """409 on request_check → poll /one pending → api_status=pending."""
    from sumsub_client import SumsubClient

    client = SumsubClient(app_token="tok", secret_key="sec")

    with patch.object(client, "_request_with_retry") as mock_req:
        # First call: request_check returns 409
        # Second call: get_applicant_review_status returns pending
        mock_req.side_effect = [
            (409, {"description": "Applicant is already in pending state"}, "Conflict"),
            (200, {"review": {"reviewStatus": "pending"}}, ""),
        ]
        result = client.request_check("app_409_pend")

    assert result["api_status"] == "pending"
    assert result.get("ok") is True
    assert result["applicant_id"] == "app_409_pend"
    assert result["source"] == "sumsub"
    assert mock_req.call_count == 2


# ── 2. request_check 409 → /one GREEN → api_status=live, matched=false ──


def test_request_check_409_green():
    """409 on request_check → poll /one GREEN → api_status=live."""
    from sumsub_client import SumsubClient

    client = SumsubClient(app_token="tok", secret_key="sec")

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.side_effect = [
            (409, {}, "Conflict"),
            (200, {
                "review": {
                    "reviewStatus": "completed",
                    "reviewResult": {"reviewAnswer": "GREEN"},
                }
            }, ""),
        ]
        result = client.request_check("app_409_green")

    assert result["api_status"] == "live"
    assert result["review_answer"] == "GREEN"
    assert result.get("ok") is True
    assert result["applicant_id"] == "app_409_green"


# ── 3. request_check 409 → /one RED → api_status=live, matched=true ──


def test_request_check_409_red():
    """409 on request_check → poll /one RED → api_status=live."""
    from sumsub_client import SumsubClient

    client = SumsubClient(app_token="tok", secret_key="sec")

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.side_effect = [
            (409, {}, "Conflict"),
            (200, {
                "review": {
                    "reviewStatus": "completed",
                    "reviewResult": {"reviewAnswer": "RED"},
                }
            }, ""),
        ]
        result = client.request_check("app_409_red")

    assert result["api_status"] == "live"
    assert result["review_answer"] == "RED"
    assert result.get("ok") is True


# ── 4. request_check 409 → /one unusable → api_status=error ──


def test_request_check_409_unusable():
    """409 on request_check → poll /one fails → api_status=error."""
    from sumsub_client import SumsubClient

    client = SumsubClient(app_token="tok", secret_key="sec")

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.side_effect = [
            (409, {}, "Conflict"),
            (500, {}, "Internal Server Error"),
        ]
        result = client.request_check("app_409_err")

    assert result["api_status"] == "error"
    assert result.get("ok") is False


# ── 5. Non-2xx responses are logged with structured fields, no secrets ──


def test_non_2xx_logging_structured(caplog):
    """Non-2xx Sumsub responses are logged with endpoint/status/body, no secrets."""
    from sumsub_client import SumsubClient

    client = SumsubClient(app_token="SUPER_SECRET_TOKEN", secret_key="SUPER_SECRET_KEY")

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (403, {"error": "Forbidden"}, "Access denied for applicant")
        with caplog.at_level(logging.WARNING, logger="sumsub_client"):
            client.request_check("app_log_test")

    # Verify structured log fields are present
    log_text = caplog.text
    assert "endpoint=" in log_text
    assert "status=403" in log_text
    assert "applicant_id=app_log_test" in log_text
    # Verify secrets are NOT logged
    assert "SUPER_SECRET_TOKEN" not in log_text
    assert "SUPER_SECRET_KEY" not in log_text


def test_non_2xx_logging_on_create_applicant(caplog):
    """Non-2xx on create_applicant also triggers structured logging."""
    from sumsub_client import SumsubClient

    client = SumsubClient(app_token="tok", secret_key="sec")

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (422, {}, "Unprocessable Entity")
        with caplog.at_level(logging.WARNING, logger="sumsub_client"):
            client.create_applicant(external_user_id="ext_123")

    log_text = caplog.text
    assert "endpoint=" in log_text
    assert "status=422" in log_text


def test_non_2xx_logging_on_review_status(caplog):
    """Non-2xx on get_applicant_review_status also triggers structured logging."""
    from sumsub_client import SumsubClient

    client = SumsubClient(app_token="tok", secret_key="sec")

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (500, {}, "Internal Server Error")
        with caplog.at_level(logging.WARNING, logger="sumsub_client"):
            client.get_applicant_review_status("app_rev_log")

    log_text = caplog.text
    assert "endpoint=" in log_text
    assert "status=500" in log_text
    assert "applicant_id=app_rev_log" in log_text


# ── 6. Gate 5 blocks pending person AML ──


def _make_screening_report(*, company_sanctions_status="live",
                            director_status="live", kyc_status="live"):
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
                "person_name": "Tariq Al-Rashid",
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
                "person_name": "Tariq Al-Rashid",
                "source": "sumsub",
                "api_status": kyc_status,
                "review_answer": "GREEN",
            }
        ],
    }


def _insert_app_for_gate5(db, screening_report):
    """Insert an application + memo that passes gates 1-4."""
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-409-retry-{suffix}"
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type,
         status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            f"ARF-R96-{suffix}",
            f"client-r96-{suffix}",
            "Retry Safe Test Ltd",
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
            json.dumps({"ai_source": "deterministic",
                         "metadata": {"ai_source": "deterministic"}}),
            "system",
            "APPROVE_WITH_CONDITIONS",
            "approved",
            8.5,
            "pass",
            "CONSISTENT",
        ),
    )
    db.commit()
    app = db.execute("SELECT * FROM applications WHERE id = ?",
                     (app_id,)).fetchone()
    return dict(app)


def test_gate5_blocks_pending_person_aml_409(db, temp_db):
    """Gate 5 must block when person AML (director) has api_status=pending."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="pending",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "pending" in message.lower()


# ── 7. Gate 5 blocks error person AML ──


def test_gate5_blocks_error_person_aml_409(db, temp_db):
    """Gate 5 must block when person AML (director) has api_status=error."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="error",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False


# ── 8. Gate 5 allows live GREEN person AML ──


def test_gate5_allows_live_green_person_aml_409(db, temp_db):
    """Gate 5 must allow when person AML (director) has api_status=live."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="live",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval, got: {message}"


# ── 9. Company KYB not_configured remains allowed ──


def test_company_kyb_not_configured_remains_allowed_409(db, temp_db):
    """company_watchlist with not_configured must still be allowed through Gate 5."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="live",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval, got: {message}"


# ── 10. End-to-end: 409 in screening flow → correct final result ──


def test_e2e_409_pending_in_screening(monkeypatch):
    """Full screening flow: request_check 409 → /one pending → api_status=pending."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")

    def fake_create(**kwargs):
        return {
            "applicant_id": "app_e2e_409",
            "external_user_id": kwargs.get("external_user_id", ""),
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

    import screening
    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create
    try:
        mock_client = MagicMock()
        # request_check returns pending (from 409 recovery)
        mock_client.request_check.return_value = {
            "ok": True,
            "applicant_id": "app_e2e_409",
            "review_status": "pending",
            "review_answer": "",
            "source": "sumsub",
            "api_status": "pending",
        }
        # Subsequent poll also returns pending
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "app_e2e_409",
            "review_status": "pending",
            "review_answer": "",
            "source": "sumsub",
            "api_status": "pending",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Tariq Al-Rashid", entity_type="Person")

        assert result["api_status"] == "pending"
        assert result["matched"] is False
    finally:
        screening.sumsub_create_applicant = original


def test_e2e_409_green_in_screening(monkeypatch):
    """Full screening flow: request_check 409 → /one GREEN → api_status=live, matched=false."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")

    def fake_create(**kwargs):
        return {
            "applicant_id": "app_e2e_green",
            "external_user_id": kwargs.get("external_user_id", ""),
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

    import screening
    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create
    try:
        mock_client = MagicMock()
        # request_check returns live (from 409 recovery — already completed GREEN)
        mock_client.request_check.return_value = {
            "ok": True,
            "applicant_id": "app_e2e_green",
            "review_status": "completed",
            "review_answer": "GREEN",
            "source": "sumsub",
            "api_status": "live",
        }
        # Subsequent poll also returns completed GREEN
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "app_e2e_green",
            "review_status": "completed",
            "review_answer": "GREEN",
            "source": "sumsub",
            "api_status": "live",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Farid Khan", entity_type="Person")

        assert result["api_status"] == "live"
        assert result["matched"] is False
    finally:
        screening.sumsub_create_applicant = original


def test_e2e_409_red_in_screening(monkeypatch):
    """Full screening flow: request_check 409 → /one RED → api_status=live, matched=true."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")

    def fake_create(**kwargs):
        return {
            "applicant_id": "app_e2e_red",
            "external_user_id": kwargs.get("external_user_id", ""),
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

    import screening
    original = screening.sumsub_create_applicant
    screening.sumsub_create_applicant = fake_create
    try:
        mock_client = MagicMock()
        mock_client.request_check.return_value = {
            "ok": True,
            "applicant_id": "app_e2e_red",
            "review_status": "completed",
            "review_answer": "RED",
            "source": "sumsub",
            "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "app_e2e_red",
            "review_status": "completed",
            "review_answer": "RED",
            "source": "sumsub",
            "api_status": "live",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Sanctioned Person", entity_type="Person")

        assert result["api_status"] == "live"
        assert result["matched"] is True
    finally:
        screening.sumsub_create_applicant = original
