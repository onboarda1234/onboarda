"""
RegMind — Lightweight Structured Logging / Observability
Sprint 1.5: Basic observability layer (no heavy dependencies)
Sprint 4: System logs branded as RegMind (internal identity)

Usage:
    from observability import arie_logger, log_request_start, log_request_end, log_decision

    log_request_start(handler="MemoGenerateHandler", application_id="app123")
    log_decision(decision="APPROVE", risk="MEDIUM", confidence=0.78, application_id="app123")
    log_request_end(handler="MemoGenerateHandler", application_id="app123", status=200, duration_ms=1523)
"""
import logging
import json
import time
import os
from datetime import datetime, timezone
from functools import wraps


# ── Configure structured JSON logger ──
_LOG_LEVEL = os.environ.get("ARIE_LOG_LEVEL", "INFO").upper()
_LOG_FORMAT = os.environ.get("ARIE_LOG_FORMAT", "json")  # "json" or "text"


class StructuredFormatter(logging.Formatter):
    """Emit logs as single-line JSON for easy parsing by log aggregators."""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any extra structured fields
        if hasattr(record, "structured_data"):
            log_entry.update(record.structured_data)
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable format for development."""

    def format(self, record):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        extra = ""
        if hasattr(record, "structured_data"):
            kv = " ".join(f"{k}={v}" for k, v in record.structured_data.items())
            extra = f" | {kv}"
        return f"[{ts}] {record.levelname:5s} {record.name}: {record.getMessage()}{extra}"


def _make_logger(name="arie"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        if _LOG_FORMAT == "json":
            handler.setFormatter(StructuredFormatter())
        else:
            handler.setFormatter(TextFormatter())
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, _LOG_LEVEL, logging.INFO))
    return logger


arie_logger = _make_logger("arie")


# ── Structured log helpers ──

def _log(level, message, **kwargs):
    """Emit a structured log entry with arbitrary key-value fields."""
    record = arie_logger.makeRecord(
        name=arie_logger.name,
        level=level,
        fn="", lno=0, msg=message, args=(), exc_info=None,
    )
    record.structured_data = {k: v for k, v in kwargs.items() if v is not None}
    arie_logger.handle(record)


def log_request_start(handler, **kwargs):
    """Log the start of an HTTP request or pipeline invocation."""
    _log(logging.INFO, "request_start", handler=handler, **kwargs)


def log_request_end(handler, status=200, duration_ms=None, **kwargs):
    """Log the end of a request with status and timing."""
    _log(logging.INFO, "request_end", handler=handler, status=status,
         duration_ms=duration_ms, **kwargs)


def log_error(message, handler=None, **kwargs):
    """Log an error event."""
    _log(logging.ERROR, message, handler=handler, **kwargs)


def log_decision(decision, risk=None, confidence=None, **kwargs):
    """Log a key compliance decision point."""
    _log(logging.INFO, "compliance_decision", decision=decision,
         risk_level=risk, confidence=confidence, **kwargs)


def log_pipeline_step(step, application_id=None, **kwargs):
    """Log a pipeline step (rule engine, validation, supervisor)."""
    _log(logging.INFO, f"pipeline_step:{step}", application_id=application_id, **kwargs)


def log_validation_result(status, quality_score=None, critical_count=0, **kwargs):
    """Log validation engine result."""
    _log(logging.INFO, "validation_result", status=status,
         quality_score=quality_score, critical_issues=critical_count, **kwargs)


def log_supervisor_verdict(verdict, contradictions=0, warnings=0, can_approve=None, **kwargs):
    """Log supervisor verdict."""
    _log(logging.INFO, "supervisor_verdict", verdict=verdict,
         contradictions=contradictions, warnings=warnings,
         can_approve=can_approve, **kwargs)


def log_ai_model_usage(model, agent, input_tokens=0, output_tokens=0, cost_usd=0.0,
                       routing_reason=None, application_id=None, **kwargs):
    """Sprint 3.5: Log AI model usage with cost tracking for observability."""
    _log(logging.INFO, "ai_model_usage", model=model, agent=agent,
         input_tokens=input_tokens, output_tokens=output_tokens,
         cost_usd=round(cost_usd, 4), routing_reason=routing_reason,
         application_id=application_id, **kwargs)


def log_cost_comparison(application_id, actual_model, actual_cost,
                        alternative_model, alternative_cost, savings_pct, **kwargs):
    """Sprint 3.5: Log cost comparison for routing decisions."""
    _log(logging.INFO, "cost_comparison", application_id=application_id,
         actual_model=actual_model, actual_cost=round(actual_cost, 4),
         alternative_model=alternative_model, alternative_cost=round(alternative_cost, 4),
         savings_pct=round(savings_pct, 1), **kwargs)


# ── Timer decorator for handler methods ──

def timed(handler_name=None):
    """Decorator to automatically log request start/end with timing."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            name = handler_name or func.__qualname__
            start = time.monotonic()
            log_request_start(handler=name)
            try:
                result = func(*args, **kwargs)
                elapsed = round((time.monotonic() - start) * 1000, 1)
                log_request_end(handler=name, status=200, duration_ms=elapsed)
                return result
            except Exception as e:
                elapsed = round((time.monotonic() - start) * 1000, 1)
                log_error(str(e), handler=name, duration_ms=elapsed)
                raise
        return wrapper
    return decorator
