"""
Tests for screening.py — Unit tests for screening functions with mocks.
Covers geolocate_ip, lookup_opencorporates, sumsub_verify_webhook,
_simulate_aml_screen, _simulate_ip_geolocation, run_full_screening.
"""
import os
import hmac
import hashlib
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestGeolocateIp:
    """Test geolocate_ip() with various inputs."""

    def test_localhost_ipv4(self):
        from screening import geolocate_ip
        result = geolocate_ip("127.0.0.1")
        assert result["country"] == "Local"
        assert result["source"] == "local"
        assert result["risk_level"] == "LOW"

    def test_localhost_ipv6(self):
        from screening import geolocate_ip
        result = geolocate_ip("::1")
        assert result["country"] == "Local"
        assert result["source"] == "local"

    def test_zero_ip(self):
        from screening import geolocate_ip
        result = geolocate_ip("0.0.0.0")
        assert result["source"] == "local"

    def test_none_ip(self):
        from screening import geolocate_ip
        result = geolocate_ip(None)
        assert result["source"] == "local"

    def test_empty_ip(self):
        from screening import geolocate_ip
        result = geolocate_ip("")
        assert result["source"] == "local"

    def test_localhost_returns_all_fields(self):
        from screening import geolocate_ip
        result = geolocate_ip("127.0.0.1")
        assert "country" in result
        assert "country_code" in result
        assert "is_vpn" in result
        assert "is_proxy" in result
        assert "is_tor" in result
        assert "risk_level" in result
        assert "checked_at" in result


class TestSimulateAmlScreen:
    """Test _simulate_aml_screen() fallback behavior."""

    def test_returns_required_fields(self):
        from screening import _simulate_aml_screen
        result = _simulate_aml_screen("John Doe")
        assert "matched" in result
        assert "results" in result
        assert "source" in result
        assert result["source"] == "simulated"
        assert "screened_at" in result

    def test_returns_list_of_results(self):
        from screening import _simulate_aml_screen
        result = _simulate_aml_screen("Jane Smith")
        assert isinstance(result["results"], list)

    def test_custom_note(self):
        from screening import _simulate_aml_screen
        result = _simulate_aml_screen("Test Person", note="Custom note")
        assert result["note"] == "Custom note"


class TestSimulateIpGeolocation:
    """Test _simulate_ip_geolocation() fallback."""

    def test_returns_required_fields(self):
        from screening import _simulate_ip_geolocation
        result = _simulate_ip_geolocation("8.8.8.8")
        assert "country" in result
        assert "country_code" in result
        assert "source" in result
        assert result["source"] == "simulated"

    def test_custom_note(self):
        from screening import _simulate_ip_geolocation
        result = _simulate_ip_geolocation("8.8.8.8", note="API unavailable")
        assert result["note"] == "API unavailable"


class TestSimulateCompanyLookup:
    """Test _simulate_company_lookup() fallback."""

    def test_returns_required_fields(self):
        from screening import _simulate_company_lookup
        result = _simulate_company_lookup("Test Corp")
        assert "found" in result
        assert "companies" in result
        assert "source" in result
        assert result["source"] == "simulated"

    def test_company_name_in_result(self):
        from screening import _simulate_company_lookup
        result = _simulate_company_lookup("Acme Inc")
        if result["companies"]:
            assert result["companies"][0]["name"] == "Acme Inc"


class TestSumsubVerifyWebhook:
    """Test sumsub_verify_webhook() signature verification."""

    def test_valid_signature(self, monkeypatch):
        import screening
        secret = "test-webhook-secret"
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", secret)

        body = b'{"type":"applicantReviewed"}'
        expected_sig = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()

        result = screening.sumsub_verify_webhook(body, expected_sig)
        assert result is True

    def test_invalid_signature(self, monkeypatch):
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "test-secret")

        body = b'{"type":"applicantReviewed"}'
        result = screening.sumsub_verify_webhook(body, "invalid-signature")
        assert result is False

    def test_no_secret_in_dev_mode(self, monkeypatch):
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "")
        monkeypatch.setattr(screening, "ENVIRONMENT", "demo")

        body = b'{"type":"test"}'
        result = screening.sumsub_verify_webhook(body, "any-sig")
        assert result is True

    def test_no_secret_in_production_rejected(self, monkeypatch):
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "")
        monkeypatch.setattr(screening, "ENVIRONMENT", "production")

        body = b'{"type":"test"}'
        result = screening.sumsub_verify_webhook(body, "any-sig")
        assert result is False


class TestSumsubSign:
    """Test _sumsub_sign() HMAC signature generation."""

    def test_returns_required_headers(self, monkeypatch):
        import screening
        monkeypatch.setattr(screening, "SUMSUB_APP_TOKEN", "test-token")
        monkeypatch.setattr(screening, "SUMSUB_SECRET_KEY", "test-secret")

        headers = screening._sumsub_sign("GET", "/resources/applicants")
        assert "X-App-Token" in headers
        assert "X-App-Access-Ts" in headers
        assert "X-App-Access-Sig" in headers
        assert headers["X-App-Token"] == "test-token"

    def test_timestamp_is_numeric(self, monkeypatch):
        import screening
        monkeypatch.setattr(screening, "SUMSUB_APP_TOKEN", "test-token")
        monkeypatch.setattr(screening, "SUMSUB_SECRET_KEY", "test-secret")

        headers = screening._sumsub_sign("POST", "/test")
        assert headers["X-App-Access-Ts"].isdigit()


class TestLookupOpencorporates:
    """Test lookup_opencorporates() with mocked API."""

    def test_no_api_key_simulates(self, monkeypatch):
        import screening
        monkeypatch.setattr(screening, "OPENCORPORATES_API_KEY", "")
        result = screening.lookup_opencorporates("Test Corp")
        assert result["source"] == "simulated"

    @patch("screening.requests.get")
    def test_api_success(self, mock_get, monkeypatch):
        import screening
        monkeypatch.setattr(screening, "OPENCORPORATES_API_KEY", "test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": {
                "companies": [
                    {"company": {"name": "Test Corp", "company_number": "123", "jurisdiction_code": "mu", "current_status": "Active"}}
                ],
                "total_count": 1
            }
        }
        mock_get.return_value = mock_response

        result = screening.lookup_opencorporates("Test Corp", "mu")
        assert result["found"] is True
        assert result["source"] == "opencorporates"
        assert len(result["companies"]) == 1

    @patch("screening.requests.get")
    def test_api_error_simulates(self, mock_get, monkeypatch):
        import screening
        monkeypatch.setattr(screening, "OPENCORPORATES_API_KEY", "test-key")

        mock_get.side_effect = Exception("Connection error")
        result = screening.lookup_opencorporates("Test Corp")
        assert result["source"] == "simulated"


class TestRunFullScreening:
    """Test run_full_screening() end-to-end with mocks."""

    def test_full_screening_structure(self, mock_screening):
        from screening import run_full_screening
        app_data = {"company_name": "Test Corp Ltd", "country": "Mauritius"}
        directors = [{"full_name": "John Doe", "nationality": "British"}]
        ubos = [{"full_name": "Jane Smith", "nationality": "Mauritian", "ownership_pct": 80}]

        report = run_full_screening(app_data, directors, ubos, client_ip="192.168.1.1")
        assert "screened_at" in report
        assert "company_screening" in report
        assert "director_screenings" in report
        assert "ubo_screenings" in report
        assert "ip_geolocation" in report
        assert "overall_flags" in report
        assert "total_hits" in report

    def test_full_screening_no_directors(self, mock_screening):
        from screening import run_full_screening
        app_data = {"company_name": "Solo Corp", "country": "UK"}
        report = run_full_screening(app_data, [], [], client_ip=None)
        assert report["director_screenings"] == []
        assert report["ubo_screenings"] == []

    def test_full_screening_with_ip(self, mock_screening):
        from screening import run_full_screening
        app_data = {"company_name": "Test Corp", "country": "Mauritius"}
        report = run_full_screening(app_data, [], [], client_ip="8.8.8.8")
        assert "ip_geolocation" in report

    def test_full_screening_jurisdiction_mapping(self, mock_screening):
        from screening import run_full_screening
        # Test various country mappings
        for country in ["Mauritius", "United Kingdom", "Singapore", "France"]:
            app_data = {"company_name": "Test Corp", "country": country}
            report = run_full_screening(app_data, [], [])
            assert "company_screening" in report


class TestSimulatedSubsumApplicant:
    """Test _simulate_sumsub_applicant() fallback."""

    def test_returns_required_fields(self):
        from screening import _simulate_sumsub_applicant
        result = _simulate_sumsub_applicant("user123", "John", "Doe")
        assert "applicant_id" in result
        assert "external_user_id" in result
        assert result["source"] == "simulated"
        assert result["external_user_id"] == "user123"

    def test_deterministic_id(self):
        from screening import _simulate_sumsub_applicant
        r1 = _simulate_sumsub_applicant("user123")
        r2 = _simulate_sumsub_applicant("user123")
        assert r1["applicant_id"] == r2["applicant_id"]

    def test_different_users_different_ids(self):
        from screening import _simulate_sumsub_applicant
        r1 = _simulate_sumsub_applicant("user1")
        r2 = _simulate_sumsub_applicant("user2")
        assert r1["applicant_id"] != r2["applicant_id"]


class TestSimulatedSubsumToken:
    """Test _simulate_sumsub_token() fallback."""

    def test_returns_required_fields(self):
        from screening import _simulate_sumsub_token
        result = _simulate_sumsub_token("user123")
        assert "token" in result
        assert "user_id" in result
        assert result["source"] == "simulated"

    def test_token_is_base64(self):
        import base64
        from screening import _simulate_sumsub_token
        result = _simulate_sumsub_token("user123")
        # Should be decodable base64
        decoded = base64.b64decode(result["token"])
        assert b"sim_token_user123" in decoded
