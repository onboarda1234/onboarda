"""PR8 verification provider reliability guards."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from document_verification import verify_document_layered
from verification_failure_taxonomy import (
    FAILURE_RETRYABLE_TRANSIENT,
    FAILURE_TERMINAL_INVALID_REQUEST,
    build_provider_failure_result,
    classify_verification_provider_failure,
    format_verification_failure_log_line,
    is_pii_decryption_noise,
)


class FakeProviderError(Exception):
    def __init__(self, message, *, status_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def test_claude_400_invalid_pdf_is_terminal_invalid_request():
    err = FakeProviderError(
        "Error code: 400 - invalid_request_error: PDF specified was not valid",
        status_code=400,
        body={"error": {"type": "invalid_request_error", "message": "PDF specified was not valid"}},
    )

    failure = classify_verification_provider_failure(err, provider="claude")

    assert failure["classification"] == FAILURE_TERMINAL_INVALID_REQUEST
    assert failure["reason_code"] == "claude_invalid_pdf"
    assert failure["retryable"] is False
    assert failure["provider_status_code"] == 400
    assert failure["provider_error_type"] == "invalid_request_error"


def test_claude_400_nested_anthropic_error_type_is_preserved():
    err = FakeProviderError(
        "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'PDF specified was not valid'}}",
        status_code=400,
        body={"type": "error", "error": {"type": "invalid_request_error", "message": "PDF specified was not valid"}},
    )

    failure = classify_verification_provider_failure(err, provider="claude")

    assert failure["classification"] == FAILURE_TERMINAL_INVALID_REQUEST
    assert failure["reason_code"] == "claude_invalid_pdf"
    assert failure["provider_error_type"] == "invalid_request_error"


def test_transient_provider_failures_are_retryable():
    err = TimeoutError("Claude request timeout while sending document")

    failure = classify_verification_provider_failure(err, provider="claude")

    assert failure["classification"] == FAILURE_RETRYABLE_TRANSIENT
    assert failure["reason_code"] == "provider_transient_error"
    assert failure["retryable"] is True


def test_provider_failure_result_is_failed_not_generic_flagged():
    err = FakeProviderError(
        "invalid_request_error: PDF specified was not valid token=secret-value",
        status_code=400,
    )

    result = build_provider_failure_result(err, provider="claude")

    assert result["overall"] == "failed"
    assert result["verification_failure_classification"] == FAILURE_TERMINAL_INVALID_REQUEST
    assert result["provider_failure"] is True
    assert result["verification_failure"]["classification"] == FAILURE_TERMINAL_INVALID_REQUEST
    assert result["checks"][0]["source"] == "provider_error"
    assert result["checks"][0]["result"] == "fail"
    assert "secret-value" not in result["ai_error"]


def test_pii_decryption_noise_is_separately_marked_not_pdf_cause():
    message = "PII decryption failed for field 'nationality': Invalid encryption token"

    failure = classify_verification_provider_failure(RuntimeError(message), provider="claude")

    assert is_pii_decryption_noise(message) is True
    assert failure["pii_context_signal"] is True
    assert failure["reason_code"] != "claude_invalid_pdf"


def test_verification_failure_log_line_is_parseable_and_pii_safe():
    failure = {
        "classification": FAILURE_TERMINAL_INVALID_REQUEST,
        "reason_code": "claude_invalid_pdf",
        "provider": "claude",
        "operation": "document_verification",
        "retryable": False,
        "provider_status_code": 400,
        "provider_error_type": "invalid_request_error",
        "model": "claude-sonnet-4-6",
    }

    line = format_verification_failure_log_line(
        failure,
        environment="staging",
        document_id="doc_123",
        application_id="app_456",
        doc_type="passport",
        mime_type="application/pdf",
        file_size=2 * 1024 * 1024,
        status="failed",
    )

    assert line.startswith("verification_provider_telemetry ")
    assert "event=verification_provider_failure" in line
    assert "classification=terminal_invalid_request" in line
    assert "reason_code=claude_invalid_pdf" in line
    assert "provider_status=400" in line
    assert "mime_type=application_pdf" in line
    assert "file_size_band=1mb_5mb" in line
    assert "doc_name" not in line
    assert "nationality" not in line


def test_layered_verification_preserves_provider_failure_as_failed(tmp_path):
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%invalid-test-payload\n")

    class FakeClaude:
        def extract_document_fields(self, **_kwargs):
            return {}

        def verify_document(self, **_kwargs):
            return build_provider_failure_result(
                FakeProviderError(
                    "invalid_request_error: PDF specified was not valid",
                    status_code=400,
                ),
                provider="claude",
                operation="document_verification",
            )

    result = verify_document_layered(
        doc_type="passport",
        category="person",
        file_path=str(pdf),
        file_size=pdf.stat().st_size,
        mime_type="application/pdf",
        prescreening_data={"full_name": "Test Person"},
        risk_level="MEDIUM",
        existing_hashes=[],
        claude_client=FakeClaude(),
        person_name="Test Person",
        check_overrides=[{
            "id": "AI-TEST",
            "label": "AI Test Check",
            "classification": "ai",
            "type": "validity",
            "rule": "Use provider for test",
        }],
        file_name="bad.pdf",
    )

    assert result["overall"] == "failed"
    assert result["verification_failure_classification"] == FAILURE_TERMINAL_INVALID_REQUEST
    assert result["provider_failure"] is True
    assert result["retryable"] is False
    assert result["verification_failure"]["reason_code"] == "claude_invalid_pdf"
    assert any(check.get("source") == "provider_error" for check in result["checks"])


def test_layered_verification_surfaces_field_extraction_provider_failure(tmp_path):
    pdf = tmp_path / "bad-extraction.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%invalid-test-payload\n")

    class FakeClaude:
        def __init__(self):
            self.last_provider_failure = None
            self.verify_called = False

        def extract_document_fields(self, **_kwargs):
            failure = classify_verification_provider_failure(
                FakeProviderError(
                    "invalid_request_error: PDF specified was not valid",
                    status_code=400,
                ),
                provider="claude",
                operation="document_field_extraction",
            )
            self.last_provider_failure = failure
            return {}

        def verify_document(self, **_kwargs):
            self.verify_called = True
            return {"checks": [{"result": "pass", "label": "Should Not Run"}], "overall": "verified"}

    fake = FakeClaude()
    result = verify_document_layered(
        doc_type="passport",
        category="person",
        file_path=str(pdf),
        file_size=pdf.stat().st_size,
        mime_type="application/pdf",
        prescreening_data={"full_name": "Test Person"},
        risk_level="MEDIUM",
        existing_hashes=[],
        claude_client=fake,
        person_name="Test Person",
        check_overrides=[{
            "id": "AI-TEST",
            "label": "AI Test Check",
            "classification": "ai",
            "type": "validity",
            "rule": "Use provider for test",
        }],
        file_name="bad-extraction.pdf",
    )

    assert result["overall"] == "failed"
    assert result["verification_failure"]["operation"] == "document_field_extraction"
    assert result["verification_failure"]["reason_code"] == "claude_invalid_pdf"
    assert fake.verify_called is False
