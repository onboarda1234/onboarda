"""
Tests for AML trigger path fix (P0).

Root cause:
  request_check() sent ``body=b"{}"`` to ``POST .../status/pending``.
  Sumsub's status-transition endpoint expects NO body — the empty JSON
  object with ``Content-Type: application/json`` caused a 400 for
  AML-only levels (``aml-screening``).

Secondary fix:
  sumsub_create_applicant() did not forward ``dob`` and ``country`` to
  ``client.create_applicant()``, so AML applicants were created without
  DOB/country in fixedInfo.

Covers:
  1. request_check sends POST with no body (body=None)
  2. Raw 400 response body is logged in the returned error dict
  3. Successful AML-only trigger returns correct api_status=live
  4. sumsub_create_applicant forwards dob & country
  5. No regression to Gate 5 gating logic
  6. Full AML flow succeeds (create → trigger → poll → GREEN)
"""
import os
import tempfile

# Ensure DB_PATH is set before production-module import triggers config.py.
if "DB_PATH" not in os.environ:
    os.environ["DB_PATH"] = os.path.join(
        tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db"
    )

from unittest.mock import MagicMock, patch, call


# ── 1. request_check sends POST with no body ──


def test_request_check_sends_no_body():
    """request_check must POST with body=None (no JSON body) to /status/pending."""
    from sumsub_client import SumsubClient

    client = SumsubClient(
        app_token="test_token",
        secret_key="test_secret",
    )

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (200, {}, "")
        client.request_check("applicant_aml_001")

    mock_req.assert_called_once()
    args, kwargs = mock_req.call_args
    method, path = args[0], args[1]
    body = kwargs.get("body", args[2] if len(args) > 2 else None)
    assert method == "POST"
    assert "/resources/applicants/applicant_aml_001/status/pending" in path
    assert body is None, (
        f"request_check must send body=None (no JSON body), got {body!r}"
    )


# ── 2. Raw 400 response body is included in error result ──


def test_request_check_400_includes_raw_body():
    """When Sumsub returns 400, the error dict must include the raw response body."""
    from sumsub_client import SumsubClient

    client = SumsubClient(
        app_token="test_token",
        secret_key="test_secret",
    )

    raw_body = '{"description":"Bad request — unexpected body","code":400}'

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (400, {"description": "Bad request"}, raw_body)
        result = client.request_check("app_400_test")

    assert result["api_status"] == "error"
    assert result.get("response_body") == raw_body
    assert result.get("endpoint", "").endswith("/status/pending")
    assert result.get("method") == "POST"
    assert result.get("status_code") == 400
    assert result.get("applicant_id") == "app_400_test"


# ── 3. Successful AML-only trigger returns api_status=live ──


def test_request_check_success_returns_live():
    """Successful POST /status/pending returns ok=True, api_status=live."""
    from sumsub_client import SumsubClient

    client = SumsubClient(
        app_token="test_token",
        secret_key="test_secret",
    )

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (200, {"ok": 1}, "")
        result = client.request_check("app_success")

    assert result["ok"] is True
    assert result["api_status"] == "live"
    assert result["source"] == "sumsub"


# ── 4. sumsub_create_applicant forwards dob & country ──


def test_create_applicant_forwards_dob_and_country():
    """sumsub_create_applicant must pass dob and country to client.create_applicant."""
    mock_client = MagicMock()
    mock_client.create_applicant.return_value = {
        "applicant_id": "app_dob",
        "external_user_id": "ext_123",
        "status": "init",
        "source": "sumsub",
        "api_status": "live",
    }

    with patch("screening.get_sumsub_client", return_value=mock_client):
        import screening
        screening.sumsub_create_applicant(
            external_user_id="ext_123",
            first_name="John",
            last_name="Doe",
            dob="1990-01-15",
            country="MU",
            level_name="aml-screening",
        )

    mock_client.create_applicant.assert_called_once()
    call_kwargs = mock_client.create_applicant.call_args[1]
    assert call_kwargs["dob"] == "1990-01-15", (
        f"dob not forwarded: {call_kwargs}"
    )
    assert call_kwargs["country"] == "MU", (
        f"country not forwarded: {call_kwargs}"
    )


# ── 5. Full AML flow: create → trigger (no body) → poll → GREEN ──


def test_full_aml_flow_green(monkeypatch):
    """End-to-end AML screening: applicant created, trigger with no body, poll → GREEN."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")
    monkeypatch.delenv("SUMSUB_COMPANY_LEVEL_NAME", raising=False)

    captured_create = {}

    def fake_create(**kwargs):
        captured_create.update(kwargs)
        return {
            "applicant_id": "app_aml_green",
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
            "applicant_id": "app_aml_green",
            "review_status": "completed",
            "review_answer": "GREEN",
            "source": "sumsub",
            "api_status": "live",
        }
        with patch("screening.get_sumsub_client", return_value=mock_client):
            result = screening.screen_sumsub_aml(
                "John Doe",
                birth_date="1990-01-15",
                nationality="MU",
                entity_type="Person",
            )

        assert result["api_status"] == "live"
        assert result["matched"] is False
        assert result["source"] == "sumsub"
        mock_client.request_check.assert_called_once_with("app_aml_green")
        mock_client.get_applicant_review_status.assert_called_once_with("app_aml_green")
    finally:
        screening.sumsub_create_applicant = original


# ── 6. Gate 5 unchanged: no bypass ──


def test_gate5_still_blocks_error_after_fix(db, temp_db):
    """Gate 5 still blocks api_status=error director screening after fix."""
    import json
    import uuid
    from security_hardening import ApprovalGateValidator

    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-aml-trig-{suffix}"
    screening_report = {
        "company_screening": {
            "found": True,
            "source": "opencorporates",
            "api_status": "live",
            "sanctions": {
                "matched": False, "results": [], "source": "sumsub",
                "api_status": "not_configured",
                "reason": "Sumsub company KYB level not configured",
            },
        },
        "director_screenings": [{
            "person_name": "Jane Doe",
            "screening": {
                "matched": False, "results": [],
                "source": "sumsub",
                "api_status": "error",
            },
        }],
        "ubo_screenings": [],
        "ip_geolocation": {"source": "ipapi", "api_status": "live", "risk_level": "LOW"},
        "kyc_applicants": [{
            "person_name": "Jane Doe",
            "source": "sumsub",
            "api_status": "live",
            "review_answer": "GREEN",
        }],
    }
    db.execute(
        """INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type,
         status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, f"ARF-AT-{suffix}", f"client-at-{suffix}", "AML Trig Test Ltd",
         "Mauritius", "Technology", "SME", "compliance_review", "MEDIUM", 45,
         json.dumps({"screening_report": screening_report})),
    )
    db.execute(
        """INSERT INTO compliance_memos
        (application_id, memo_data, generated_by, ai_recommendation,
         review_status, quality_score, validation_status, supervisor_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (app_id, json.dumps({"ai_source": "deterministic",
                              "metadata": {"ai_source": "deterministic"}}),
         "system", "APPROVE_WITH_CONDITIONS", "approved", 8.5, "pass", "CONSISTENT"),
    )
    db.commit()
    app = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    app = dict(app)

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False
    assert "director_screening" in message


# ── 7. request_check 409 still polls canonical review state ──


def test_request_check_409_still_polls():
    """409 handling is not regressed — still polls GET .../one."""
    from sumsub_client import SumsubClient

    client = SumsubClient(
        app_token="test_token",
        secret_key="test_secret",
    )

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (409, {}, "state conflict")
        with patch.object(client, "get_applicant_review_status") as mock_poll:
            mock_poll.return_value = {
                "applicant_id": "app_409",
                "review_status": "completed",
                "review_answer": "GREEN",
                "source": "sumsub",
                "api_status": "live",
            }
            result = client.request_check("app_409")

    assert result["ok"] is True
    assert result["api_status"] == "live"
    mock_poll.assert_called_once_with("app_409")
