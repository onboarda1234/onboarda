"""
Upload latency telemetry guards.

These tests keep the request-finish telemetry scoped and parseable so
CloudWatch queries can calculate p50/p95 without touching upload behavior.
"""
import logging
import time
from types import SimpleNamespace


def test_upload_latency_route_context_is_scoped_to_upload_and_verify():
    from base_handler import _upload_latency_route_context

    upload = _upload_latency_route_context("POST", "/api/applications/app_123/documents")
    assert upload == {
        "operation": "document_upload",
        "path_template": "/api/applications/{application_id}/documents",
        "application_id": "app_123",
    }

    verify = _upload_latency_route_context("POST", "/api/documents/doc_456/verify")
    assert verify == {
        "operation": "document_verify",
        "path_template": "/api/documents/{document_id}/verify",
        "document_id": "doc_456",
    }

    assert _upload_latency_route_context("GET", "/api/applications/app_123/documents") is None
    assert _upload_latency_route_context("POST", "/api/applications/app_123/notes") is None
    assert _upload_latency_route_context("POST", "/api/documents/ai-verify") is None


def test_upload_latency_log_line_is_cloudwatch_parse_ready():
    from base_handler import _format_upload_latency_log_line

    line = _format_upload_latency_log_line(
        context={
            "operation": "document_upload",
            "path_template": "/api/applications/{application_id}/documents",
            "application_id": "app_123",
        },
        status=201,
        duration_ms=42.4242,
        request_bytes=2048,
        environment="staging",
    )

    assert line.startswith("upload_latency_telemetry ")
    assert "event=upload_latency_request" in line
    assert "operation=document_upload" in line
    assert "path_template=/api/applications/{application_id}/documents" in line
    assert "status=201" in line
    assert "duration_ms=42.42" in line
    assert "request_bytes=2048" in line
    assert "environment=staging" in line
    assert "application_id=app_123" in line


def test_upload_latency_content_length_parser_is_defensive():
    from base_handler import _parse_content_length

    assert _parse_content_length("123") == 123
    assert _parse_content_length(None) == 0
    assert _parse_content_length("not-a-number") == 0
    assert _parse_content_length("-1") == 0


def test_base_handler_on_finish_emits_upload_latency_log(caplog):
    from base_handler import BaseHandler

    fake_handler = SimpleNamespace(
        request=SimpleNamespace(
            method="POST",
            path="/api/documents/doc_456/verify",
            headers={"Content-Length": "4096"},
        ),
        _upload_latency_started_at=time.monotonic() - 0.01,
        get_status=lambda: 200,
    )

    with caplog.at_level(logging.INFO, logger="arie"):
        BaseHandler.on_finish(fake_handler)

    messages = [record.getMessage() for record in caplog.records]
    telemetry = [msg for msg in messages if msg.startswith("upload_latency_telemetry ")]
    assert len(telemetry) == 1
    assert "operation=document_verify" in telemetry[0]
    assert "document_id=doc_456" in telemetry[0]
    assert "status=200" in telemetry[0]
    assert "request_bytes=4096" in telemetry[0]
