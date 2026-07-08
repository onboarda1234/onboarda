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
import contextvars
import logging
import json
import re
import time
import os
import uuid
from datetime import datetime, timezone
from functools import wraps

from branding import BRAND

# ── Configure structured JSON logger ──
_LOG_LEVEL = os.environ.get("ARIE_LOG_LEVEL", "INFO").upper()
DEFAULT_CLOUDWATCH_NAMESPACE = f"{BRAND['backoffice_name']}/Pilot"


def _resolve_log_format():
    """Resolve the log format, FORCING JSON in staging/production.

    P12-9 / DCI-028: CloudWatch Logs Insights queries and incident
    reconstruction depend on single-line JSON. A deployed environment that
    sets ARIE_LOG_FORMAT=text would silently lose every structured field, so
    the override is honoured only outside staging/production.
    """
    fmt = (os.environ.get("ARIE_LOG_FORMAT", "json") or "json").strip().lower()
    if fmt not in ("json", "text"):
        fmt = "json"
    try:
        from environment import get_environment
        env = get_environment()
    except Exception:
        env = ""
    if env in ("staging", "production") and fmt != "json":
        # The logger is not configured yet — plain logging is fine here.
        logging.getLogger("arie").warning(
            "ARIE_LOG_FORMAT=%s ignored: structured JSON logging is FORCED "
            "in %s (DCI-028)", fmt, env,
        )
        return "json"
    return fmt


_LOG_FORMAT = _resolve_log_format()

# ── Request correlation (P12-9 / DCI-028) ──
# A contextvar-scoped correlation id: HTTP requests set it from the incoming
# X-Request-ID header (or generate one) in BaseHandler.prepare; the
# verification worker sets it per job. Every structured log line emitted via
# _log() carries it automatically, so an incident can be reconstructed across
# request logs, audit rows and worker logs with one Logs Insights filter.
_REQUEST_ID: "contextvars.ContextVar" = contextvars.ContextVar("arie_request_id", default=None)
_REQUEST_ID_MAX_LEN = 128
_REQUEST_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9._:-]")


def _sanitize_request_id(value):
    """Return a safe correlation id string, or None if unusable.

    Incoming X-Request-ID headers are attacker-controlled: strip anything
    outside a conservative charset (defends log injection) and bound the
    length (defends log bloat)."""
    if not value:
        return None
    cleaned = _REQUEST_ID_SAFE_RE.sub("", str(value).strip())[:_REQUEST_ID_MAX_LEN]
    return cleaned or None


def set_request_id(value=None) -> str:
    """Bind a correlation id to the current context; generates one if the
    supplied value is missing/unusable. Returns the bound id."""
    rid = _sanitize_request_id(value) or uuid.uuid4().hex
    _REQUEST_ID.set(rid)
    return rid


def get_request_id():
    """Return the current context's correlation id, or None."""
    return _REQUEST_ID.get()


def clear_request_id() -> None:
    _REQUEST_ID.set(None)


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
    """Emit a structured log entry with arbitrary key-value fields.

    P12-9 / DCI-028: the context correlation id is injected automatically —
    callers no longer need to remember to thread request ids through."""
    kwargs.setdefault("request_id", get_request_id())
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


def emit_cloudwatch_metric_log(
    metric_name,
    value,
    *,
    unit="Count",
    namespace=DEFAULT_CLOUDWATCH_NAMESPACE,
    environment=None,
    service=None,
):
    """Emit a low-cardinality metric log for CloudWatch metric filters.

    The payload intentionally excludes application, customer, document, and job
    identifiers. CloudWatch metric filters can extract ``metric_value`` while
    alarms keep dimensions limited to environment/service.
    """

    if not metric_name:
        return
    try:
        metric_value = float(value)
    except (TypeError, ValueError):
        return

    payload = {
        "metric_namespace": namespace,
        "metric_name": str(metric_name),
        "metric_value": metric_value,
        "metric_unit": unit,
        "environment": environment or os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT", "unknown"),
    }
    if service:
        payload["service"] = service
    _log(logging.INFO, "cloudwatch_metric", **payload)


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
