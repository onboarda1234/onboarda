"""
Tests for Sumsub error enrichment parity.

Every outbound Sumsub request path that can fail with a provider-side
4xx/5xx must return structured error metadata:
  - response_body
  - endpoint
  - method
  - status_code

This file verifies each method in SumsubClient against that contract.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
import pytest


# ── Helpers ──

ENRICHMENT_KEYS = ("response_body", "endpoint", "method", "status_code")


def _make_client():
    from sumsub_client import SumsubClient
    return SumsubClient(app_token="test_token", secret_key="test_secret")


def _assert_enriched(result, expected_method, endpoint_fragment, status_code, body_fragment):
    """Assert the result dict contains the expected structured error metadata."""
    assert result["api_status"] == "error", f"Expected api_status=error, got {result.get('api_status')}"
    for key in ENRICHMENT_KEYS:
        assert key in result, f"Missing key '{key}' in error result: {result}"
    assert result["method"] == expected_method
    assert result["status_code"] == status_code
    assert endpoint_fragment in result["endpoint"]
    assert body_fragment in result["response_body"]


# ── 1. create_applicant 400 ──

def test_create_applicant_400_returns_enriched_error():
    client = _make_client()
    raw = '{"description":"Invalid country code","code":400}'
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (400, {}, raw)
        result = client.create_applicant(external_user_id="ext_1")
    _assert_enriched(result, "POST", "/resources/applicants", 400, "Invalid country code")


# ── 2. get_applicant_by_external_id 404 ──

def test_get_applicant_by_external_id_404_returns_enriched_error():
    client = _make_client()
    raw = '{"description":"Applicant not found","code":404}'
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (404, {}, raw)
        result = client.get_applicant_by_external_id("ext_missing")
    _assert_enriched(result, "GET", "/resources/applicants", 404, "Applicant not found")


# ── 3. get_applicant_status 500 ──

def test_get_applicant_status_500_returns_enriched_error():
    client = _make_client()
    raw = '{"description":"Internal server error","code":500}'
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (500, {}, raw)
        result = client.get_applicant_status("app_500")
    _assert_enriched(result, "GET", "/resources/applicants/app_500/one", 500, "Internal server error")


# ── 4. add_document 400 ──

def test_add_document_400_returns_enriched_error():
    client = _make_client()
    raw = '{"description":"Invalid document type","code":400}'
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (400, {}, raw)
        result = client.add_document(
            applicant_id="app_doc_err",
            doc_type="PASSPORT",
            file_data=b"dummy",
        )
    _assert_enriched(result, "POST", "/resources/applicants/app_doc_err/info/idDoc", 400, "Invalid document type")
    assert result["applicant_id"] == "app_doc_err"


# ── 5. get_verification_result 403 ──

def test_get_verification_result_403_returns_enriched_error():
    client = _make_client()
    raw = '{"description":"Forbidden","code":403}'
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (403, {}, raw)
        result = client.get_verification_result("app_vr_err")
    _assert_enriched(result, "GET", "/resources/applicants/app_vr_err/verification/result", 403, "Forbidden")


# ── 6. get_applicant_review_status 502 ──

def test_get_applicant_review_status_502_returns_enriched_error():
    client = _make_client()
    raw = '{"description":"Bad gateway","code":502}'
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (502, {}, raw)
        result = client.get_applicant_review_status("app_rs_err")
    _assert_enriched(result, "GET", "/resources/applicants/app_rs_err/one", 502, "Bad gateway")


# ── 7. get_aml_screening 404 ──

def test_get_aml_screening_404_returns_enriched_error():
    client = _make_client()
    raw = '{"description":"Not found for AML-only level","code":404}'
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (404, {}, raw)
        result = client.get_aml_screening("app_aml_err")
    _assert_enriched(result, "GET", "/resources/applicants/app_aml_err/checkSteps", 404, "Not found for AML-only level")


# ── 8. generate_access_token 401 ──

def test_generate_access_token_401_returns_enriched_error():
    client = _make_client()
    raw = '{"description":"Unauthorized","code":401}'
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (401, {}, raw)
        result = client.generate_access_token("ext_tok_err")
    _assert_enriched(result, "POST", "/resources/accessTokens", 401, "Unauthorized")


# ── 9. request_check 400 (regression guard — must still work) ──

def test_request_check_400_still_returns_enriched_error():
    client = _make_client()
    raw = '{"description":"Bad request — unexpected body","code":400}'
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (400, {}, raw)
        result = client.request_check("app_rc_400")
    _assert_enriched(result, "POST", "/status/pending", 400, "Bad request")


# ── 10. add_document error uses _error_result (api_status=error, not plain dict) ──

def test_add_document_error_has_api_status_error():
    """add_document failures must use _error_result so they include api_status=error."""
    client = _make_client()
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (422, {}, "Unprocessable")
        result = client.add_document("app_x", "SELFIE", b"img")
    assert result["api_status"] == "error"
    assert result["status"] == "error"
    assert result["status_code"] == 422


# ── 11. generate_access_token calls _log_non_2xx on failure ──

def test_generate_access_token_logs_non_2xx(caplog):
    """generate_access_token must call _log_non_2xx for structured logging."""
    import logging
    client = _make_client()
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (403, {}, "Forbidden")
        with caplog.at_level(logging.WARNING, logger="sumsub_client"):
            client.generate_access_token("ext_log")
    non_2xx_logged = any("non-2xx" in r.message.lower() for r in caplog.records)
    failure_logged = any(
        "generate_access_token" in r.message and "failure" in r.message.lower()
        for r in caplog.records
    )
    assert non_2xx_logged and failure_logged, (
        "Expected both structured non-2xx log and generate_access_token failure log"
    )


# ── 12. Response body is truncated to 500 chars ──

def test_response_body_truncated_to_500():
    client = _make_client()
    long_body = "x" * 1000
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (400, {}, long_body)
        result = client.get_applicant_status("app_trunc")
    assert len(result["response_body"]) <= 500


# ── 13. No secrets in error results ──

def test_no_secrets_in_error_result():
    """Error results must never contain app_token or secret_key values."""
    client = _make_client()
    raw = '{"description":"Some error","code":400}'
    with patch.object(client, "_request_with_retry") as m:
        m.return_value = (400, {}, raw)
        result = client.create_applicant(external_user_id="ext_sec")
    serialized = str(result)
    assert "test_token" not in serialized
    assert "test_secret" not in serialized
