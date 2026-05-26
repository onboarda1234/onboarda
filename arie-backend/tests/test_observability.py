"""
Tests for observability.py — Structured logging, formatters, log helpers, and timed decorator.
"""
import json
import logging
import time
import pytest


class TestStructuredFormatter:
    """Test StructuredFormatter JSON output."""

    def test_json_output(self):
        from observability import StructuredFormatter
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "test message"
        assert "timestamp" in parsed

    def test_structured_data_included(self):
        from observability import StructuredFormatter
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        record.structured_data = {"key": "value", "count": 42}
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["key"] == "value"
        assert parsed["count"] == 42

    def test_exception_info_included(self):
        from observability import StructuredFormatter
        formatter = StructuredFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="error", args=(), exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]


class TestTextFormatter:
    """Test TextFormatter human-readable output."""

    def test_text_output(self):
        from observability import TextFormatter
        formatter = TextFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "INFO" in output
        assert "test message" in output

    def test_text_with_structured_data(self):
        from observability import TextFormatter
        formatter = TextFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        record.structured_data = {"handler": "TestHandler"}
        output = formatter.format(record)
        assert "handler=TestHandler" in output


class TestLogHelpers:
    """Test structured log helper functions."""

    def test_log_request_start(self, caplog):
        from observability import log_request_start
        with caplog.at_level(logging.DEBUG, logger="arie"):
            log_request_start(handler="TestHandler", application_id="app123")
        # Should not raise

    def test_log_request_end(self, caplog):
        from observability import log_request_end
        with caplog.at_level(logging.DEBUG, logger="arie"):
            log_request_end(handler="TestHandler", status=200, duration_ms=100)
        # Should not raise

    def test_log_error(self, caplog):
        from observability import log_error
        with caplog.at_level(logging.DEBUG, logger="arie"):
            log_error("Something went wrong", handler="ErrorHandler")
        # Should not raise

    def test_log_decision(self, caplog):
        from observability import log_decision
        with caplog.at_level(logging.DEBUG, logger="arie"):
            log_decision(decision="APPROVE", risk="MEDIUM", confidence=0.85)
        # Should not raise

    def test_log_pipeline_step(self, caplog):
        from observability import log_pipeline_step
        with caplog.at_level(logging.DEBUG, logger="arie"):
            log_pipeline_step(step="rule_engine", application_id="app123")
        # Should not raise

    def test_log_validation_result(self, caplog):
        from observability import log_validation_result
        with caplog.at_level(logging.DEBUG, logger="arie"):
            log_validation_result(status="PASS", quality_score=0.92, critical_count=0)
        # Should not raise

    def test_log_supervisor_verdict(self, caplog):
        from observability import log_supervisor_verdict
        with caplog.at_level(logging.DEBUG, logger="arie"):
            log_supervisor_verdict(
                verdict="APPROVE", contradictions=0, warnings=1, can_approve=True
            )
        # Should not raise

    def test_log_ai_model_usage(self, caplog):
        from observability import log_ai_model_usage
        with caplog.at_level(logging.DEBUG, logger="arie"):
            log_ai_model_usage(
                model="claude-sonnet-4-6", agent="risk_assessment",
                input_tokens=1000, output_tokens=500, cost_usd=0.0125,
                routing_reason="LOW risk", application_id="app123"
            )
        # Should not raise

    def test_log_cost_comparison(self, caplog):
        from observability import log_cost_comparison
        with caplog.at_level(logging.DEBUG, logger="arie"):
            log_cost_comparison(
                application_id="app123",
                actual_model="claude-sonnet-4-6", actual_cost=0.01,
                alternative_model="claude-opus-4-6", alternative_cost=0.05,
                savings_pct=80.0,
            )
        # Should not raise


class TestTimedDecorator:
    """Test timed() decorator for handler timing."""

    def test_timed_function_runs(self):
        from observability import timed

        @timed("test_handler")
        def sample_func():
            return "result"

        result = sample_func()
        assert result == "result"

    def test_timed_function_with_args(self):
        from observability import timed

        @timed("test_handler")
        def add(a, b):
            return a + b

        assert add(3, 4) == 7

    def test_timed_function_preserves_exception(self):
        from observability import timed

        @timed("test_handler")
        def failing_func():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            failing_func()

    def test_timed_auto_name(self):
        from observability import timed

        @timed()
        def auto_named_func():
            return 42

        assert auto_named_func() == 42


class TestArieLogger:
    """Test arie_logger singleton."""

    def test_logger_exists(self):
        from observability import arie_logger
        assert arie_logger is not None
        assert arie_logger.name == "arie"

    def test_logger_has_handlers(self):
        from observability import arie_logger
        assert len(arie_logger.handlers) > 0
