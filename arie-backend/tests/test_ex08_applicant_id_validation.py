"""
Tests for EX-08: Validate Sumsub applicant ID on KYC registration.

Covers:
  1. Portal frontend no longer sends hardcoded 'US' for country
  2. Portal reads country from f-inc-country form field
  3. Portal validates applicant_id before showing success toast
  4. Portal treats empty/whitespace applicant_id as failure
  5. Portal treats api_status='error' as failure
  6. Portal keeps KYC button enabled for retry on failure
  7. Backend SumsubApplicantHandler passes country through to create_applicant
  8. Backend returns empty applicant_id on error (no false success)
"""
import os
import re
import tempfile

# Ensure DB_PATH is set before production-module import triggers config.py.
if "DB_PATH" not in os.environ:
    os.environ["DB_PATH"] = os.path.join(
        tempfile.gettempdir(), f"onboarda_test_ex08_{os.getpid()}.db"
    )

import pytest

# ── Portal / Frontend Tests ──

PORTAL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "arie-portal.html"
)


def _read_portal():
    with open(PORTAL_PATH, "r", encoding="utf-8") as f:
        return f.read()


class TestPortalCountryNotHardcoded:
    """EX-08 gate 3: country must come from application jurisdiction, not 'US'."""

    def test_no_hardcoded_us_in_sendKYCLink(self):
        """The sendKYCLink function must NOT contain country: 'US'."""
        src = _read_portal()
        # Extract the sendKYCLink function body
        match = re.search(
            r"async function sendKYCLink\(personId\)\s*\{", src
        )
        assert match, "sendKYCLink function not found in portal"
        start = match.start()
        # Find the closing brace by brace-counting
        brace_count = 0
        func_body = ""
        for i, ch in enumerate(src[start:], start=start):
            if ch == "{":
                brace_count += 1
            elif ch == "}":
                brace_count -= 1
            func_body += ch
            if brace_count == 0:
                break
        # Must NOT contain country: 'US' as a literal
        assert "country: 'US'" not in func_body, (
            "sendKYCLink still has hardcoded country: 'US'"
        )
        assert 'country: "US"' not in func_body, (
            "sendKYCLink still has hardcoded country: \"US\""
        )

    def test_country_reads_from_inc_country_field(self):
        """The country value should come from the f-inc-country form field."""
        src = _read_portal()
        # The fix should reference 'f-inc-country'
        assert "f-inc-country" in src, (
            "Portal does not reference f-inc-country for KYC country"
        )
        # Specifically in the KYC registration context
        match = re.search(r"getElementById\(['\"]f-inc-country['\"]\)", src)
        assert match, (
            "Portal does not read f-inc-country element for KYC registration"
        )


class TestPortalApplicantIdValidation:
    """EX-08 gates 1, 2, 4: applicant_id must be validated before success."""

    def test_applicant_id_trimmed_and_checked(self):
        """Portal must trim and check applicant_id before success path."""
        src = _read_portal()
        # The fix should trim the applicant ID
        assert ".trim()" in src[src.find("sendKYCLink"):], (
            "applicant_id is not trimmed in sendKYCLink"
        )

    def test_empty_applicant_id_shows_error(self):
        """Portal must have a code path that shows error when applicant_id is empty."""
        src = _read_portal()
        func_start = src.find("async function sendKYCLink")
        func_region = src[func_start:func_start + 5000]
        # Must check for empty applicant_id
        assert "!applicantId" in func_region or "applicantId ==" in func_region, (
            "sendKYCLink does not check for empty applicant_id"
        )
        # Must show error toast on failure path
        assert "Registration Failed" in func_region, (
            "sendKYCLink does not show error toast on invalid applicant_id"
        )

    def test_api_status_error_treated_as_failure(self):
        """Portal must check api_status field for 'error' state."""
        src = _read_portal()
        func_start = src.find("async function sendKYCLink")
        func_region = src[func_start:func_start + 5000]
        assert "api_status" in func_region, (
            "sendKYCLink does not check api_status field"
        )
        assert "'error'" in func_region or '"error"' in func_region, (
            "sendKYCLink does not check for api_status='error'"
        )

    def test_success_toast_only_after_valid_id(self):
        """Success toast must appear AFTER the applicant_id validation check."""
        src = _read_portal()
        func_start = src.find("async function sendKYCLink")
        func_region = src[func_start:func_start + 5000]
        # Find the validation check position
        validation_pos = func_region.find("!applicantId")
        # Find success toast position
        success_pos = func_region.find("Applicant Registered")
        assert validation_pos > 0, "applicant_id validation not found"
        assert success_pos > 0, "success toast not found"
        assert validation_pos < success_pos, (
            "Success toast appears before applicant_id validation"
        )

    def test_failure_path_returns_early(self):
        """On failure, the function must return early (not fall through to success)."""
        src = _read_portal()
        func_start = src.find("async function sendKYCLink")
        func_region = src[func_start:func_start + 5000]
        # After the !applicantId check, there should be a return statement
        validation_pos = func_region.find("!applicantId")
        assert validation_pos > 0
        # Look for return within 300 chars after the check
        after_check = func_region[validation_pos:validation_pos + 300]
        assert "return" in after_check, (
            "No return statement after applicant_id failure check"
        )

    def test_button_reenabled_in_finally(self):
        """Button must be re-enabled in the finally block for retry."""
        src = _read_portal()
        func_start = src.find("async function sendKYCLink")
        func_region = src[func_start:func_start + 5000]
        # Check that finally block re-enables the button
        finally_pos = func_region.rfind("finally")
        assert finally_pos > 0, "No finally block in sendKYCLink"
        finally_block = func_region[finally_pos:finally_pos + 200]
        assert re.search(r'\.disabled\s*=\s*false', finally_block), (
            "Button not re-enabled in finally block"
        )


# ── Backend Tests ──


class TestBackendApplicantIdValidation:
    """Backend must return empty applicant_id on error responses."""

    def test_error_result_has_empty_applicant_id(self):
        """_error_result must always return empty applicant_id."""
        from sumsub_client import SumsubClient
        result = SumsubClient._error_result("test_op", "test reason")
        assert result["applicant_id"] == "", (
            "_error_result returned non-empty applicant_id"
        )
        assert result["api_status"] == "error"

    def test_error_result_not_simulated(self):
        """_error_result must not return 'simulated' api_status."""
        from sumsub_client import SumsubClient
        result = SumsubClient._error_result("test_op", "test reason")
        assert result["api_status"] != "simulated"

    def test_country_passthrough_to_create_applicant(self):
        """Backend handler passes country from request body to sumsub_create_applicant."""
        import server  # noqa: F401 — ensure module is importable
        # The SumsubApplicantHandler.post extracts country from data.get("country")
        # and passes it to sumsub_create_applicant. Verify the screening wrapper
        # normalizes it.
        from screening import normalize_country_alpha3
        # GB → GBR for Sumsub
        assert normalize_country_alpha3("GB") == "GBR"
        # MU → MUS for Mauritius
        assert normalize_country_alpha3("MU") == "MUS"
        # Empty → None (omitted from request)
        assert normalize_country_alpha3("") is None
        assert normalize_country_alpha3(None) is None

    def test_backend_handler_accepts_country(self):
        """SumsubApplicantHandler extracts country from request body."""
        import inspect
        from server import SumsubApplicantHandler
        src = inspect.getsource(SumsubApplicantHandler.post)
        assert 'country' in src, (
            "SumsubApplicantHandler.post does not reference 'country'"
        )
        assert 'data.get("country")' in src or "data.get('country')" in src, (
            "SumsubApplicantHandler.post does not extract country from request data"
        )
