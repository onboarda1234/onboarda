"""Structured observability helpers for ComplyAdvantage screening paths."""

import json
import logging
import os
import re
from datetime import datetime, timezone
from inspect import Parameter, signature
from uuid import uuid4

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME

CA_METRIC_NAMESPACE = "RegMind/Screening/ComplyAdvantage"
CA_OPERATIONAL_LOG_GROUP = "/regmind/ca/operational/"
CA_AUDIT_LOG_GROUP = "/regmind/ca/audit/"

EVENT_CLASS_OPERATIONAL = "operational"
EVENT_CLASS_AUDIT = "audit"

_OPERATIONAL_LOGGER = logging.getLogger("regmind.ca.operational")
_AUDIT_LOGGER = logging.getLogger("regmind.ca.audit")

_HIGH_CARDINALITY_DIMENSIONS = {
    "TraceId",
    "ApplicationId",
    "ClientId",
    "CustomerIdentifier",
    "CaseIdentifier",
    "AlertId",
    "RiskId",
    "Path",
    "RawPath",
    "Username",
    "Email",
}

_PROTECTED_DIMENSION_KEYS = {
    "Authorization",
    "Cookie",
    "Signature",
    "Secret",
    "Password",
    "Token",
    "AccessToken",
    "BearerToken",
    "ApiKey",
}

_METRIC_NAME_ALIASES = {
    "webhook_async_processing_failure": "WebhookAsyncProcessingFailures",
    "normalized_write_failure": "NormalizedWriteFailures",
    "monitoring_alerts_write_failure": "MonitoringAlertsWriteFailures",
    "agent_7_push_failure": "Agent7PushFailures",
    "subscription_update_failure": "SubscriptionUpdateFailures",
}


def new_trace_id(prefix="ca"):
    return f"{prefix}-{uuid4().hex}"


def inbound_trace_id(value, *, max_len=128):
    if not value:
        return new_trace_id()
    value = str(value).strip()
    if not value or len(value) > max_len:
        return new_trace_id()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return new_trace_id()
    return value


def accepts_keyword(callable_obj, keyword):
    try:
        parameters = signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind == Parameter.VAR_KEYWORD or parameter.name == keyword
        for parameter in parameters
    )


def emit_operational(event_name, *, level=logging.INFO, trace_id=None, component="unknown", outcome="success", **fields):
    payload = _base_payload(
        EVENT_CLASS_OPERATIONAL,
        event_name,
        level,
        trace_id=trace_id,
        component=component,
        outcome=outcome,
        log_group=CA_OPERATIONAL_LOG_GROUP,
        fields=fields,
    )
    _OPERATIONAL_LOGGER.log(level, json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return payload


def emit_audit(event_name, *, level=logging.INFO, trace_id=None, component="unknown", outcome="success", **fields):
    payload = _base_payload(
        EVENT_CLASS_AUDIT,
        event_name,
        level,
        trace_id=trace_id,
        component=component,
        outcome=outcome,
        log_group=CA_AUDIT_LOG_GROUP,
        fields=fields,
    )
    _AUDIT_LOGGER.log(level, json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return payload


def emit_metric(
    name,
    *,
    metric_name=None,
    value=1,
    unit="Count",
    dimensions=None,
    event_class=EVENT_CLASS_OPERATIONAL,
    trace_id=None,
    component="unknown",
    outcome="success",
    level=logging.INFO,
    **fields,
):
    metric_name = metric_name or _METRIC_NAME_ALIASES.get(name) or _pascal_case(name)
    dimensions = _safe_dimensions(dimensions or _default_dimensions(fields))
    payload_fields = {
        **fields,
        "metric_event": name,
        "metric_name": metric_name,
        "metric_namespace": CA_METRIC_NAMESPACE,
        "metric_value": value,
        "metric_unit": unit,
        "metric_dimensions": dimensions,
    }
    event_name = f"ca_metric_{name}"
    if event_class == EVENT_CLASS_AUDIT:
        return emit_audit(
            event_name,
            level=level,
            trace_id=trace_id,
            component=component,
            outcome=outcome,
            **payload_fields,
        )
    return emit_operational(
        event_name,
        level=level,
        trace_id=trace_id,
        component=component,
        outcome=outcome,
        **payload_fields,
    )


def status_family(status_code=None, *, error=None):
    if error is not None:
        name = error.__class__.__name__.lower()
        if "timeout" in name:
            return "timeout"
        return "network_error"
    try:
        status = int(status_code)
    except (TypeError, ValueError):
        return "not_applicable"
    if status == 429:
        return "429"
    return f"{status // 100}xx"


def endpoint_category(path):
    path = path_template(path)
    if path.startswith("/oauth") or "token" in path:
        return "auth"
    if path.startswith("/v2/cases/"):
        return "case"
    if path.startswith("/v2/alerts/") and path.endswith("/risks"):
        return "alert_risks"
    if path.startswith("/v2/entity-screening/risks/"):
        return "deep_risk"
    if path.startswith("/v2/workflows/"):
        return "workflow"
    if "subscription" in path:
        return "subscription"
    return "unknown"


def path_template(path):
    if not path:
        return "unknown"
    path = str(path).split("?", 1)[0]
    path = re.sub(r"/[0-9a-fA-F-]{32,36}(?=/|$)", "/{id}", path)
    # CA identifiers seen in paths may contain dots, colons, underscores, and hyphens;
    # this only normalizes logged path templates and is not used for file or URL construction.
    path = re.sub(r"/(case|alert|risk|workflow|customer|profile)[A-Za-z0-9._:-]*(?=/|$)", r"/{\1_id}", path)
    return path


def _base_payload(event_class, event_name, level, *, trace_id, component, outcome, log_group, fields):
    payload = {
        "event_class": event_class,
        "event_name": event_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": logging.getLevelName(level),
        "provider": COMPLYADVANTAGE_PROVIDER_NAME,
        "environment": _environment(),
        "active_provider": _active_provider(),
        "trace_id": trace_id or new_trace_id(),
        "component": component,
        "outcome": outcome,
        "log_group": log_group,
    }
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    return payload


def _default_dimensions(fields):
    dimensions = {
        "Environment": _environment(),
        "Provider": COMPLYADVANTAGE_PROVIDER_NAME,
    }
    mapping = {
        "active_provider": "ActiveProvider",
        "webhook_type": "WebhookType",
        "endpoint_category": "EndpointCategory",
        "status_family": "StatusFamily",
        "outcome": "Outcome",
        "step": "Step",
        "signature_mode": "Mode",
    }
    for field, dimension in mapping.items():
        value = fields.get(field)
        if value is not None:
            dimensions[dimension] = str(value)
    return dimensions


def _safe_dimensions(dimensions):
    safe = {}
    protected_tokens = {
        re.sub(r"[^a-z0-9]", "", key.lower())
        for key in _PROTECTED_DIMENSION_KEYS
    }
    for key, value in dict(dimensions).items():
        if key in _HIGH_CARDINALITY_DIMENSIONS:
            continue
        normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
        if any(token in normalized_key for token in protected_tokens):
            continue
        if value is None:
            continue
        safe[key] = str(value)
    return safe


def _pascal_case(name):
    return "".join(part.capitalize() for part in str(name).replace("-", "_").split("_") if part)


def _environment():
    return os.environ.get("ENVIRONMENT", "development").strip().lower() or "development"


def _active_provider():
    try:
        from screening_config import get_active_provider_name
        return get_active_provider_name() or "unknown"
    except Exception:
        return "unknown"
