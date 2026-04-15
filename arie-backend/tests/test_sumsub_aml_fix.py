"""
Tests for EX-06 Sumsub AML backend fix.

Covers:
  1. AML level name resolution (SUMSUB_AML_LEVEL_NAME env var)
  2. Trigger endpoint (POST .../status/pending) is called
  3. Poll endpoint (GET .../one) is called
  4. GREEN → api_status=live, matched=false
  5. RED → api_status=live, matched=true
  6. Pending → api_status=pending
  7. API error → api_status=error
  8. Gate 5 blocks pending person AML
  9. Gate 5 blocks error person AML
  10. Gate 5 allows live GREEN person AML
  11. Company KYB not_configured remains allowed
"""
import json
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


# ── 1. Environment config: SUMSUB_AML_LEVEL_NAME ──


def test_aml_level_name_explicit(monkeypatch):
    """SUMSUB_AML_LEVEL_NAME env var is used when set."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "custom-aml")
    from environment import get_sumsub_aml_level_name
    assert get_sumsub_aml_level_name() == "custom-aml"


def test_aml_level_name_default(monkeypatch):
    """Defaults to 'aml-screening' when SUMSUB_AML_LEVEL_NAME is not set."""
    monkeypatch.delenv("SUMSUB_AML_LEVEL_NAME", raising=False)
    from environment import get_sumsub_aml_level_name
    assert get_sumsub_aml_level_name() == "aml-screening"


# ── 2. screen_sumsub_aml uses AML level for person screening ──


def test_aml_uses_aml_level_name(monkeypatch):
    """Person AML screening passes SUMSUB_AML_LEVEL_NAME to create_applicant."""
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


# ── 3. Trigger endpoint is called ──


def test_trigger_endpoint_called(monkeypatch):
    """request_check (POST .../status/pending) is called after applicant creation."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")

    def fake_create(**kwargs):
        return {
            "applicant_id": "app_123",
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
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "app_123",
            "review_status": "completed",
            "review_answer": "GREEN",
            "source": "sumsub",
            "api_status": "live",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            screening.screen_sumsub_aml("Jane Doe", entity_type="Person")

        mock_client.request_check.assert_called_once_with("app_123")
    finally:
        screening.sumsub_create_applicant = original


# ── 4. Poll endpoint is called ──


def test_poll_endpoint_called(monkeypatch):
    """get_applicant_review_status (GET .../one) is called after trigger."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")

    def fake_create(**kwargs):
        return {
            "applicant_id": "app_456",
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
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "app_456",
            "review_status": "completed",
            "review_answer": "GREEN",
            "source": "sumsub",
            "api_status": "live",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            screening.screen_sumsub_aml("Jane Doe", entity_type="Person")

        mock_client.get_applicant_review_status.assert_called_once_with("app_456")
    finally:
        screening.sumsub_create_applicant = original


# ── 5. GREEN mapping ──


def test_green_mapping(monkeypatch):
    """GREEN reviewAnswer → api_status=live, matched=false."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")

    def fake_create(**kwargs):
        return {
            "applicant_id": "app_green",
            "external_user_id": "x",
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
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "app_green",
            "review_status": "completed",
            "review_answer": "GREEN",
            "source": "sumsub",
            "api_status": "live",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Clean Person", entity_type="Person")

        assert result["api_status"] == "live"
        assert result["matched"] is False
        assert result["source"] == "sumsub"
    finally:
        screening.sumsub_create_applicant = original


# ── 6. RED mapping ──


def test_red_mapping(monkeypatch):
    """RED reviewAnswer → api_status=live, matched=true."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")

    def fake_create(**kwargs):
        return {
            "applicant_id": "app_red",
            "external_user_id": "x",
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
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "app_red",
            "review_status": "completed",
            "review_answer": "RED",
            "source": "sumsub",
            "api_status": "live",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Sanctioned Person", entity_type="Person")

        assert result["api_status"] == "live"
        assert result["matched"] is True
        assert result["source"] == "sumsub"
        assert len(result["results"]) >= 1
    finally:
        screening.sumsub_create_applicant = original


# ── 7. Pending mapping ──


def test_pending_mapping(monkeypatch):
    """Still processing → api_status=pending."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")

    def fake_create(**kwargs):
        return {
            "applicant_id": "app_pend",
            "external_user_id": "x",
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
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "app_pend",
            "review_status": "pending",
            "review_answer": "",
            "source": "sumsub",
            "api_status": "pending",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Pending Person", entity_type="Person")

        assert result["api_status"] == "pending"
        assert result["matched"] is False
    finally:
        screening.sumsub_create_applicant = original


# ── 8. API error mapping ──


def test_api_error_mapping(monkeypatch):
    """True API failure → api_status=error."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")

    def fake_create(**kwargs):
        return {
            "applicant_id": "app_err",
            "external_user_id": "x",
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
            "ok": True, "source": "sumsub", "api_status": "live",
        }
        mock_client.get_applicant_review_status.return_value = {
            "applicant_id": "app_err",
            "status": "error",
            "source": "sumsub",
            "api_status": "error",
            "error": "get_applicant_review_status failed: API returned 500",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Error Person", entity_type="Person")

        assert result["api_status"] == "error"
        assert result["matched"] is False
    finally:
        screening.sumsub_create_applicant = original


def test_api_error_on_trigger_failure(monkeypatch):
    """Trigger endpoint failure → api_status=error."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")

    def fake_create(**kwargs):
        return {
            "applicant_id": "app_trig_err",
            "external_user_id": "x",
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
            "ok": False,
            "source": "sumsub",
            "api_status": "error",
            "error": "request_check failed: API returned 500",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml("Trigger Error", entity_type="Person")

        assert result["api_status"] == "error"
    finally:
        screening.sumsub_create_applicant = original


# ── 9-10. Gate 5: blocks pending/error person AML, allows live GREEN ──


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
    """Insert an application + memo that passes gates 1-4."""
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-aml-fix-{suffix}"
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type,
         status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            f"ARF-AF-{suffix}",
            f"client-af-{suffix}",
            "AML Fix Test Ltd",
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


def test_gate5_blocks_pending_person_aml(db, temp_db):
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
    assert "director_screening" in message


def test_gate5_blocks_error_person_aml(db, temp_db):
    """Gate 5 must block when person AML (director) has api_status=error."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="error",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "director_screening" in message


def test_gate5_allows_live_green_person_aml(db, temp_db):
    """Gate 5 must allow when person AML (director) has api_status=live."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="live",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval, got: {message}"


# ── 11. Company KYB not_configured remains allowed ──


def test_company_kyb_not_configured_remains_allowed(db, temp_db):
    """company_watchlist with not_configured must still be allowed through Gate 5."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="live",
    )
    app = _insert_app_for_gate5(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Expected approval, got: {message}"


def test_company_screening_returns_not_configured(monkeypatch):
    """screen_sumsub_aml for Company with no company level → not_configured."""
    monkeypatch.delenv("SUMSUB_COMPANY_LEVEL_NAME", raising=False)
    from screening import screen_sumsub_aml
    result = screen_sumsub_aml("Acme Corp", entity_type="Company")
    assert result["api_status"] == "not_configured"
    assert result["matched"] is False


# ── 12. Sumsub client: request_check and get_applicant_review_status ──


def test_sumsub_client_request_check_endpoint():
    """request_check calls POST /resources/applicants/{id}/status/pending."""
    from sumsub_client import SumsubClient

    client = SumsubClient(
        app_token="test_token",
        secret_key="test_secret",
    )

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (200, {}, "")
        result = client.request_check("applicant_xyz")

    assert result["ok"] is True
    mock_req.assert_called_once()
    call_args = mock_req.call_args
    assert call_args[0][0] == "POST"
    assert "/resources/applicants/applicant_xyz/status/pending" in call_args[0][1]


def test_sumsub_client_get_review_status_endpoint():
    """get_applicant_review_status calls GET /resources/applicants/{id}/one."""
    from sumsub_client import SumsubClient

    client = SumsubClient(
        app_token="test_token",
        secret_key="test_secret",
    )

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (200, {
            "review": {
                "reviewStatus": "completed",
                "reviewResult": {"reviewAnswer": "GREEN"},
            }
        }, "")
        result = client.get_applicant_review_status("applicant_xyz")

    assert result["review_answer"] == "GREEN"
    assert result["api_status"] == "live"
    mock_req.assert_called_once()
    call_args = mock_req.call_args
    assert call_args[0][0] == "GET"
    assert "/resources/applicants/applicant_xyz/one" in call_args[0][1]


def test_sumsub_client_review_pending_status():
    """get_applicant_review_status maps pending reviewStatus → api_status=pending."""
    from sumsub_client import SumsubClient

    client = SumsubClient(
        app_token="test_token",
        secret_key="test_secret",
    )

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (200, {
            "review": {
                "reviewStatus": "pending",
            }
        }, "")
        result = client.get_applicant_review_status("applicant_xyz")

    assert result["api_status"] == "pending"
    assert result["review_answer"] == ""


# ── 13. determine_screening_mode treats pending as unknown ──


def test_screening_mode_unknown_when_director_pending(temp_db):
    """determine_screening_mode treats pending director screening as unknown."""
    from security_hardening import determine_screening_mode

    report = _make_screening_report(
        company_sanctions_status="not_configured",
        director_status="pending",
    )
    assert determine_screening_mode(report) == "unknown"
