"""
Tests for demo safety features: fallback memo, pre-validation, observability.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


class TestPreValidation:
    """Pre-validate application data before entering AI pipeline."""

    def test_valid_application_passes(self, temp_db):
        from server import pre_validate_application
        valid, errors = pre_validate_application({
            "company_name": "Test Corp", "country": "Mauritius",
            "sector": "Technology", "entity_type": "SME"
        })
        assert valid is True
        assert len(errors) == 0

    def test_missing_company_name_fails(self, temp_db):
        from server import pre_validate_application
        valid, errors = pre_validate_application({
            "country": "Mauritius", "sector": "Technology", "entity_type": "SME"
        })
        assert valid is False
        assert any(e["field"] == "company_name" for e in errors)

    def test_empty_country_fails(self, temp_db):
        from server import pre_validate_application
        valid, errors = pre_validate_application({
            "company_name": "Test", "country": "", "sector": "Tech", "entity_type": "SME"
        })
        assert valid is False
        assert any(e["field"] == "country" for e in errors)

    def test_none_input_fails(self, temp_db):
        from server import pre_validate_application
        valid, errors = pre_validate_application(None)
        assert valid is False

    def test_empty_dict_fails(self, temp_db):
        from server import pre_validate_application
        valid, errors = pre_validate_application({})
        assert valid is False
        assert len(errors) >= 1  # at least one error reported


class TestFallbackMemo:
    """Fallback memo is safe, structured, and conservative."""

    def test_fallback_memo_structure(self, temp_db):
        from server import generate_fallback_memo
        memo = generate_fallback_memo()
        assert "sections" in memo
        assert "metadata" in memo
        assert memo["metadata"]["is_fallback"] is True
        assert memo["metadata"]["approval_recommendation"] == "REJECT"

    def test_fallback_memo_with_application(self, temp_db):
        from server import generate_fallback_memo
        memo = generate_fallback_memo({"company_name": "Acme Ltd", "country": "UK", "sector": "Fintech"})
        assert "Acme Ltd" in memo["sections"]["executive_summary"]["content"]
        assert "UK" in memo["sections"]["risk_assessment"]["sub_sections"]["jurisdiction_risk"]["content"]

    def test_fallback_passes_validation(self, temp_db):
        """Fallback memo must survive the validation engine without crashing."""
        from server import generate_fallback_memo, validate_compliance_memo
        memo = generate_fallback_memo({"company_name": "Test", "country": "MU", "sector": "Tech"})
        result = validate_compliance_memo(memo)
        assert result is not None
        assert "issues" in result

    def test_fallback_passes_supervisor(self, temp_db):
        """Fallback memo must survive the supervisor without crashing."""
        from server import generate_fallback_memo, run_memo_supervisor
        memo = generate_fallback_memo()
        result = run_memo_supervisor(memo)
        assert result is not None
        assert "verdict" in result


class TestObservability:
    """Structured logging module works correctly."""

    def test_logger_exists(self):
        from observability import arie_logger
        assert arie_logger is not None

    def test_log_functions_dont_crash(self):
        from observability import (
            log_request_start, log_request_end, log_error,
            log_decision, log_pipeline_step,
            log_validation_result, log_supervisor_verdict
        )
        # None of these should raise
        log_request_start(handler="TestHandler", application_id="app1")
        log_request_end(handler="TestHandler", status=200, duration_ms=100)
        log_error("test error", handler="TestHandler")
        log_decision(decision="APPROVE", risk="LOW", confidence=0.85)
        log_pipeline_step(step="validation", application_id="app1")
        log_validation_result(status="pass", quality_score=8.5, critical_count=0)
        log_supervisor_verdict(verdict="CONSISTENT", contradictions=0)

    def test_timed_decorator(self):
        from observability import timed
        import time

        @timed("test_func")
        def slow_func():
            time.sleep(0.01)
            return 42

        result = slow_func()
        assert result == 42
