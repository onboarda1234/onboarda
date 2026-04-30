import json
import logging
from pathlib import Path

from screening_complyadvantage.observability import (
    CA_AUDIT_LOG_GROUP,
    CA_METRIC_NAMESPACE,
    CA_OPERATIONAL_LOG_GROUP,
    EVENT_CLASS_AUDIT,
    EVENT_CLASS_OPERATIONAL,
    emit_audit,
    emit_metric,
    emit_operational,
    inbound_trace_id,
)


def test_operational_and_audit_events_have_distinct_schema_and_log_groups(caplog, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("SCREENING_PROVIDER", "sumsub")

    with caplog.at_level(logging.INFO):
        operational = emit_operational(
            "ca_webhook_received",
            trace_id="trace-safe",
            component="webhook_handler",
            outcome="success",
        )
        audit = emit_audit(
            "ca_provider_truth_persisted",
            trace_id="trace-safe",
            component="webhook_storage",
            outcome="success",
            source_screening_report_hash="hash-1",
        )

    assert operational["event_class"] == EVENT_CLASS_OPERATIONAL
    assert operational["log_group"] == CA_OPERATIONAL_LOG_GROUP
    assert audit["event_class"] == EVENT_CLASS_AUDIT
    assert audit["log_group"] == CA_AUDIT_LOG_GROUP
    assert "ca_webhook_received" in caplog.text
    assert "ca_provider_truth_persisted" in caplog.text


def test_metric_payload_uses_namespace_and_rejects_high_cardinality_dimensions():
    payload = emit_metric(
        "webhook_delivery",
        metric_name="WebhookDeliveries",
        trace_id="trace-safe",
        component="webhook_handler",
        outcome="success",
        webhook_type="CASE_CREATED",
        dimensions={
            "Environment": "testing",
            "Provider": "complyadvantage",
            "CaseIdentifier": "case-1",
            "TraceId": "trace-safe",
            "WebhookType": "CASE_CREATED",
        },
    )

    assert payload["metric_namespace"] == CA_METRIC_NAMESPACE
    assert payload["metric_name"] == "WebhookDeliveries"
    assert payload["metric_dimensions"]["WebhookType"] == "CASE_CREATED"
    assert "CaseIdentifier" not in payload["metric_dimensions"]
    assert "TraceId" not in payload["metric_dimensions"]


def test_observability_payloads_do_not_log_secret_material(caplog):
    with caplog.at_level(logging.INFO, logger="regmind.ca.operational"):
        emit_operational(
            "ca_api_response",
            trace_id="trace-safe",
            component="client",
            outcome="success",
            path_template="/v2/cases/{id}",
            status_code=200,
        )

    rendered = caplog.text
    assert "password" not in rendered
    assert "access_token" not in rendered
    assert "Authorization" not in rendered
    assert "x-complyadvantage-signature" not in rendered


def test_inbound_trace_id_accepts_bounded_safe_header_and_rejects_unsafe_values():
    assert inbound_trace_id("req-123_ABC") == "req-123_ABC"
    assert inbound_trace_id("contains space").startswith("ca-")
    assert inbound_trace_id("path/like:value").startswith("ca-")
    assert inbound_trace_id("x" * 129).startswith("ca-")


def test_cloudwatch_observability_artifacts_parse_and_reference_known_metrics():
    repo_root = Path(__file__).resolve().parents[2]
    cloudwatch = repo_root / "docs" / "observability" / "cloudwatch"
    dashboard = json.loads((cloudwatch / "ca_dashboard.json").read_text())
    alarms = json.loads((cloudwatch / "ca_alarm_definitions.json").read_text())

    assert dashboard["namespace"] == CA_METRIC_NAMESPACE
    assert alarms["namespace"] == CA_METRIC_NAMESPACE
    dashboard_metrics = {
        metric
        for widget in dashboard["widgets"]
        for metric in widget["metrics"]
    }
    for alarm in alarms["alarms"]:
        assert alarm["metric"] in dashboard_metrics or alarm["metric"].startswith("WebhookFetch")

    for query_file in cloudwatch.glob("ca_*.cwlogs"):
        query = query_file.read_text()
        assert "RegMind/Screening/ComplyAdvantage" in query.replace("\\/", "/")
        assert "raw_payload" not in query
        assert "request_body" not in query
        assert "response_body" not in query
