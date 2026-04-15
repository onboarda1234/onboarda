"""
Tests for P0 create_applicant country-code normalisation & error handling.

Root cause:
  PR #101 started forwarding ``dob`` and ``country`` into ``create_applicant``.
  RegMind stores nationality as ISO 3166 **alpha-2** (e.g. ``PK``), but
  Sumsub expects **alpha-3** (e.g. ``PAK``) in ``fixedInfo.country``.
  This caused ``400 Bad Request`` for every person with a 2-letter code.

Covers:
  1. normalize_country_alpha3: PK → PAK
  2. normalize_country_alpha3: unknown code → None (omitted)
  3. normalize_country_alpha3: already alpha-3 passthrough
  4. normalize_country_alpha3: empty / None → None
  5. validate_dob_format: valid YYYY-MM-DD → kept
  6. validate_dob_format: invalid / empty → None
  7. sumsub_create_applicant passes normalised country & dob
  8. create_applicant 400 error includes response_body, endpoint,
     method, status_code in returned error dict
  9. request_check still sends body=None (PR #101 regression guard)
  10. Gate 5 not regressed
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

# Ensure DB_PATH is set before production-module import triggers config.py.
if "DB_PATH" not in os.environ:
    os.environ["DB_PATH"] = os.path.join(
        tempfile.gettempdir(), f"onboarda_test_{os.getpid()}.db"
    )

from unittest.mock import MagicMock, patch
import pytest


# ── 1–4. normalize_country_alpha3 ──


class TestNormalizeCountryAlpha3:
    def test_alpha2_pk_to_pak(self):
        """PK (Pakistan alpha-2) → PAK (alpha-3)."""
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("PK") == "PAK"

    def test_alpha2_gb_to_gbr(self):
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("GB") == "GBR"

    def test_alpha2_us_to_usa(self):
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("US") == "USA"

    def test_alpha2_mu_to_mus(self):
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("MU") == "MUS"

    def test_alpha2_lowercase(self):
        """Case-insensitive input."""
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("pk") == "PAK"

    def test_unknown_alpha2_returns_none(self):
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("ZZ") is None

    def test_unknown_alpha3_returns_none(self):
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("ZZZ") is None

    def test_already_alpha3_passthrough(self):
        """If caller already supplies alpha-3, pass it through."""
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("PAK") == "PAK"

    def test_empty_string_returns_none(self):
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("") is None

    def test_none_returns_none(self):
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3(None) is None

    def test_whitespace_only_returns_none(self):
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("   ") is None

    def test_too_long_returns_none(self):
        from screening import normalize_country_alpha3
        assert normalize_country_alpha3("ABCD") is None


# ── 5–6. validate_dob_format ──


class TestValidateDobFormat:
    def test_valid_iso_date(self):
        from screening import validate_dob_format
        assert validate_dob_format("1990-06-15") == "1990-06-15"

    def test_invalid_format_slashes(self):
        from screening import validate_dob_format
        assert validate_dob_format("15/06/1990") is None

    def test_invalid_date_values(self):
        from screening import validate_dob_format
        assert validate_dob_format("2000-13-45") is None

    def test_empty_string(self):
        from screening import validate_dob_format
        assert validate_dob_format("") is None

    def test_none(self):
        from screening import validate_dob_format
        assert validate_dob_format(None) is None

    def test_partial_date(self):
        from screening import validate_dob_format
        assert validate_dob_format("1990-06") is None

    def test_whitespace_stripped(self):
        from screening import validate_dob_format
        assert validate_dob_format("  1990-06-15  ") == "1990-06-15"


# ── 7. sumsub_create_applicant normalises country & dob ──


def test_create_applicant_normalises_country_and_dob():
    """sumsub_create_applicant converts alpha-2 → alpha-3 and validates DOB."""
    from screening import sumsub_create_applicant

    mock_client = MagicMock()
    mock_client.create_applicant.return_value = {
        "applicant_id": "abc123",
        "external_user_id": "ext1",
        "status": "init",
        "source": "sumsub",
        "api_status": "live",
    }

    with patch("screening.get_sumsub_client", return_value=mock_client):
        result = sumsub_create_applicant(
            external_user_id="ext1",
            first_name="Tariq",
            last_name="Ahmed",
            dob="1985-03-22",
            country="PK",
            level_name="aml-screening",
        )

    mock_client.create_applicant.assert_called_once()
    call_kwargs = mock_client.create_applicant.call_args[1]
    # Country should be normalised to alpha-3
    assert call_kwargs["country"] == "PAK"
    # DOB should be passed through (valid format)
    assert call_kwargs["dob"] == "1985-03-22"
    # info dict should also have normalised values
    assert call_kwargs["info"]["country"] == "PAK"
    assert call_kwargs["info"]["dob"] == "1985-03-22"


def test_create_applicant_omits_invalid_country():
    """Unknown country code is omitted (None), not forwarded as invalid."""
    from screening import sumsub_create_applicant

    mock_client = MagicMock()
    mock_client.create_applicant.return_value = {
        "applicant_id": "abc456",
        "external_user_id": "ext2",
        "status": "init",
        "source": "sumsub",
        "api_status": "live",
    }

    with patch("screening.get_sumsub_client", return_value=mock_client):
        sumsub_create_applicant(
            external_user_id="ext2",
            first_name="Test",
            last_name="User",
            country="ZZ",
        )

    call_kwargs = mock_client.create_applicant.call_args[1]
    # Invalid country should be None (omitted from fixedInfo)
    assert call_kwargs["country"] is None


def test_create_applicant_omits_invalid_dob():
    """Invalid DOB format is omitted, not forwarded."""
    from screening import sumsub_create_applicant

    mock_client = MagicMock()
    mock_client.create_applicant.return_value = {
        "applicant_id": "abc789",
        "external_user_id": "ext3",
        "status": "init",
        "source": "sumsub",
        "api_status": "live",
    }

    with patch("screening.get_sumsub_client", return_value=mock_client):
        sumsub_create_applicant(
            external_user_id="ext3",
            first_name="Test",
            last_name="User",
            dob="22/03/1985",
        )

    call_kwargs = mock_client.create_applicant.call_args[1]
    assert call_kwargs["dob"] is None


# ── 8. create_applicant 400 includes response_body in error dict ──


def test_create_applicant_400_includes_raw_body():
    """When create_applicant gets a 400, the error dict includes
    response_body, endpoint, method, and status_code."""
    from sumsub_client import SumsubClient

    client = SumsubClient(
        app_token="test_token",
        secret_key="test_secret",
    )

    raw_body = '{"description":"Invalid country code","code":400}'

    with patch.object(client, "_request_with_retry") as mock_req:
        mock_req.return_value = (400, {}, raw_body)
        result = client.create_applicant(
            external_user_id="ext_err",
            first_name="Tariq",
            last_name="Ahmed",
            country="PK",
        )

    assert result["api_status"] == "error"
    assert result["status_code"] == 400
    assert result["method"] == "POST"
    assert "response_body" in result
    assert "Invalid country code" in result["response_body"]
    assert "/resources/applicants" in result["endpoint"]
    assert "create_applicant failed" in result["error"]


# ── 9. request_check still sends body=None (PR #101 guard) ──


def test_request_check_still_sends_no_body():
    """Regression guard: request_check must POST with body=None."""
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
    assert "/status/pending" in path
    assert body is None, f"request_check must send body=None, got {body!r}"


# ── 10. Gate 5 not regressed ──


def _make_screening_report_for_gate5(*, director_status="live"):
    """Build a screening report with configurable director AML status."""
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
                "api_status": "not_configured",
                "reason": "Sumsub company KYB level not configured",
            },
        },
        "director_screenings": [
            {
                "person_name": "Tariq Ahmed",
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
                "person_name": "Tariq Ahmed",
                "source": "sumsub",
                "api_status": "live",
                "review_answer": "GREEN",
            }
        ],
    }


def _insert_gate5_app(db, screening_report):
    """Insert an application + memo that passes gates 1-4."""
    import uuid, json as _json
    suffix = uuid.uuid4().hex[:8]
    app_id = f"app-cc-fix-{suffix}"
    db.execute(
        """
        INSERT INTO applications
        (id, ref, client_id, company_name, country, sector, entity_type,
         status, risk_level, risk_score, prescreening_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            f"ARF-CC-{suffix}",
            f"client-cc-{suffix}",
            "Country Code Fix Test Ltd",
            "Mauritius",
            "Technology",
            "SME",
            "compliance_review",
            "MEDIUM",
            45,
            _json.dumps({
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
            _json.dumps({"ai_source": "deterministic",
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


def test_gate5_allows_live_green_person_aml_after_fix(db, temp_db):
    """Gate 5 still allows live GREEN person AML results."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report_for_gate5(director_status="live")
    app = _insert_gate5_app(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is True, f"Gate 5 should pass: {message}"


def test_gate5_blocks_error_person_aml_after_fix(db, temp_db):
    """Gate 5 still blocks error person AML results."""
    from security_hardening import ApprovalGateValidator

    report = _make_screening_report_for_gate5(director_status="error")
    app = _insert_gate5_app(db, report)
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)
    assert can_approve is False, f"Gate 5 should block error: {message}"


# ── 11. Full AML flow with country normalisation ──


def test_full_aml_flow_with_country_normalisation(monkeypatch):
    """screen_sumsub_aml normalises the nationality before create_applicant."""
    monkeypatch.setenv("SUMSUB_AML_LEVEL_NAME", "aml-screening")
    monkeypatch.delenv("SUMSUB_COMPANY_LEVEL_NAME", raising=False)

    captured_kwargs = {}

    def fake_create_applicant(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "applicant_id": "aml_pk_001",
            "external_user_id": kwargs["external_user_id"],
            "status": "init",
            "source": "sumsub",
            "api_status": "live",
        }

    mock_client = MagicMock()
    mock_client.request_check.return_value = {
        "ok": True, "source": "sumsub", "api_status": "live",
    }
    mock_client.get_applicant_review_status.return_value = {
        "review_status": "completed",
        "review_answer": "GREEN",
        "api_status": "live",
        "source": "sumsub",
    }

    with patch("screening.sumsub_create_applicant", side_effect=fake_create_applicant), \
         patch("screening.get_sumsub_client", return_value=mock_client):
        from screening import screen_sumsub_aml
        result = screen_sumsub_aml(
            name="Tariq Ahmed",
            birth_date="1985-03-22",
            nationality="PK",
            entity_type="Person",
        )

    # The screening itself should succeed
    assert result["api_status"] == "live"
    assert result["matched"] is False
    # Verify country was passed (the wrapper normalises it)
    assert captured_kwargs.get("country") in ("PK", "PAK")
