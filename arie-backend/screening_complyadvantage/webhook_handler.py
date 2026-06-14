"""Tornado handler for ComplyAdvantage webhooks.

The receiver uses a durable-receipt spawn-callback hybrid: it reads the raw
body first, verifies the HMAC synchronously, parses the envelope, records a
redacted delivery row, returns HTTP 202 for known events, and then schedules
the heavier fetch-back and dual-write sequence with ``IOLoop.spawn_callback``.
If the process dies after acknowledgement, the saved receipt can be retried by
the reconciliation helper.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import time

import tornado.ioloop
from pydantic import ValidationError

from base_handler import BaseHandler
from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME

from .models.webhooks import CACaseAlertListUpdatedWebhook, CACaseCreatedWebhook, CAUnknownWebhookEnvelope
from .observability import accepts_keyword, emit_metric, emit_operational, inbound_trace_id
from .webhook_fetch import WebhookEnvelopeError, extract_case_identifier, validate_alert_identifiers
from .webhook_storage import (
    process_complyadvantage_webhook,
    record_complyadvantage_webhook_receipt,
    stable_webhook_id,
)

logger = logging.getLogger(__name__)

_LEGACY_SIGNATURE_HEADER = "x-complyadvantage-signature"
_STANDARD_WEBHOOK_ID_HEADER = "webhook-id"
_STANDARD_WEBHOOK_TIMESTAMP_HEADER = "webhook-timestamp"
_STANDARD_WEBHOOK_SIGNATURE_HEADER = "webhook-signature"
_KNOWN_TYPES = {"CASE_CREATED", "CASE_ALERT_LIST_UPDATED"}
_DEFAULT_TIMESTAMP_TOLERANCE_SECONDS = 300


class ComplyAdvantageWebhookHandler(BaseHandler):
    """POST /api/webhooks/complyadvantage."""

    def initialize(self, storage_callback=None):
        self._storage_callback = storage_callback or process_complyadvantage_webhook

    def post(self):
        body = self.request.body
        trace_id = inbound_trace_id(self.request.headers.get("X-Request-ID"))
        signature_status = _signature_status(body, self.request.headers)
        if signature_status in ("invalid", "malformed", "stale"):
            event_name = "webhook_timestamp_stale" if signature_status == "stale" else "webhook_signature_invalid"
            logger.warning(
                "ca_webhook_signature signature_mode=strict signature_status=%s body_len=%d",
                signature_status,
                len(body),
            )
            emit_metric(
                event_name,
                metric_name="WebhookSignatureFailures",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                signature_mode="strict",
                signature_status=signature_status,
                body_len=len(body),
            )
            emit_metric(
                "webhook_delivery",
                metric_name="WebhookDeliveries",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                signature_mode="strict",
                webhook_type="none",
            )
            self.set_status(401)
            return
        if signature_status == "deployed_secret_missing":
            logger.error(
                "ca_webhook_signature signature_mode=deployed_fail_closed signature_secret_configured=false environment=%s",
                _environment(),
            )
            emit_metric(
                "webhook_signature_failure",
                metric_name="WebhookSignatureFailures",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                signature_mode="deployed_fail_closed",
            )
            emit_metric(
                "env_mode_drift",
                metric_name="EnvModeDrift",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                signature_mode="deployed_fail_closed",
            )
            self.set_status(503)
            return
        if signature_status == "disabled_non_production":
            logger.warning(
                "ca_webhook_signature signature_mode=sandbox_fail_open signature_verification_disabled=true environment=%s",
                _environment(),
            )
            emit_metric(
                "env_mode_drift",
                metric_name="EnvModeDrift",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure" if _environment() == "production" else "skipped",
                signature_mode="sandbox_fail_open",
            )
        else:
            logger.info("ca_webhook_signature signature_mode=strict signature_valid=true body_len=%d", len(body))
            emit_metric(
                "webhook_signature_valid",
                metric_name="WebhookSignatureValid",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="success",
                signature_mode="strict",
                body_len=len(body),
            )
        emit_operational(
            "ca_webhook_received",
            trace_id=trace_id,
            component="webhook_handler",
            outcome="success",
            signature_mode=_metric_signature_mode(signature_status),
            body_len=len(body),
        )

        try:
            payload = json.loads(body)
        except Exception:
            logger.warning("ca_webhook_invalid_json body_len=%d", len(body))
            emit_metric(
                "webhook_malformed_payload",
                metric_name="WebhookMalformedPayloads",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                webhook_type="none",
            )
            emit_metric(
                "webhook_delivery",
                metric_name="WebhookDeliveries",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                signature_mode=_metric_signature_mode(signature_status),
                webhook_type="none",
            )
            self.set_status(400)
            return

        event_type = payload.get("webhook_type") or payload.get("type") or ""
        if event_type not in _KNOWN_TYPES:
            try:
                envelope = CAUnknownWebhookEnvelope.model_validate({**payload, "webhook_type": event_type})
            except ValidationError:
                logger.warning("ca_webhook_unknown_envelope_invalid event_type=%s", event_type, exc_info=True)
                self.set_status(400)
                return
            customer = getattr(envelope, "customer", None)
            logger.info(
                "ca_webhook_unknown_event event_type=%s case_identifier=%s customer_identifier=%s",
                envelope.webhook_type,
                envelope.case_identifier,
                getattr(customer, "identifier", None),
            )
            emit_metric(
                "webhook_unknown_event",
                metric_name="WebhookUnknownEvents",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="success",
                webhook_type="UNKNOWN",
                case_identifier=envelope.case_identifier,
                customer_identifier=getattr(customer, "identifier", None),
            )
            emit_metric(
                "webhook_delivery",
                metric_name="WebhookDeliveries",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="no_op",
                signature_mode=_metric_signature_mode(signature_status),
                webhook_type="UNKNOWN",
            )
            self.set_status(202)
            return

        try:
            _validate_known_payload(event_type, payload)
            envelope = _parse_known_envelope(event_type, payload)
        except (ValidationError, WebhookEnvelopeError):
            logger.warning("ca_webhook_envelope_invalid event_type=%s", event_type, exc_info=True)
            emit_metric(
                "webhook_malformed_payload",
                metric_name="WebhookMalformedPayloads",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                webhook_type=event_type or "none",
            )
            self.set_status(400)
            return

        if event_type == "CASE_ALERT_LIST_UPDATED" and not envelope.alert_identifiers:
            logger.info(
                "ca_webhook_empty_alert_identifiers event_type=%s case_identifier=%s no_op=true",
                event_type,
                envelope.case_identifier,
            )
            emit_metric(
                "webhook_delivery",
                metric_name="WebhookDeliveries",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="no_op",
                signature_mode=_metric_signature_mode(signature_status),
                webhook_type=event_type,
            )
            self.set_status(202)
            return

        webhook_id = _standard_webhook_id(self.request.headers) or stable_webhook_id(payload, body=body)
        try:
            record_complyadvantage_webhook_receipt(
                envelope,
                webhook_id=webhook_id,
                trace_id=trace_id,
                payload=payload,
            )
        except Exception:
            logger.error(
                "ca_webhook_receipt_persist_failed webhook_type=%s case_identifier=%s",
                event_type,
                envelope.case_identifier,
                exc_info=True,
            )
            emit_metric(
                "webhook_receipt_persist_failed",
                metric_name="WebhookReceiptPersistFailures",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                webhook_type=event_type,
                case_identifier=envelope.case_identifier,
            )
            self.set_status(503)
            return

        emit_metric(
            "webhook_delivery",
            metric_name="WebhookDeliveries",
            trace_id=trace_id,
            component="webhook_handler",
            outcome="success",
            signature_mode=_metric_signature_mode(signature_status),
            webhook_type=event_type,
            case_identifier=envelope.case_identifier,
            customer_identifier=getattr(envelope.customer, "identifier", None),
        )
        self.set_status(202)
        tornado.ioloop.IOLoop.current().spawn_callback(
            self._process_webhook_async,
            envelope,
            trace_id=trace_id,
            webhook_id=webhook_id,
        )

    async def _process_webhook_async(self, envelope, trace_id=None, webhook_id=None):
        try:
            await _call_storage_callback(self._storage_callback, envelope, trace_id, webhook_id)
        except Exception:
            logger.error(
                "ca_webhook_async_processing_failure webhook_type=%s case_identifier=%s",
                getattr(envelope, "webhook_type", None),
                getattr(envelope, "case_identifier", None),
                exc_info=True,
            )
            emit_metric(
                "webhook_async_processing_failure",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                webhook_type=getattr(envelope, "webhook_type", None),
                case_identifier=getattr(envelope, "case_identifier", None),
            )


def _verify_signature(body, headers):
    secret = os.environ.get("COMPLYADVANTAGE_WEBHOOK_SECRET", "")
    if not secret or not isinstance(secret, str):
        return False
    if _has_standard_webhook_headers(headers):
        return _verify_standard_webhook_signature(body, headers, secret)
    return _verify_legacy_signature(body, headers.get(_LEGACY_SIGNATURE_HEADER, ""), secret)


def _verify_legacy_signature(body, signature, secret):
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _verify_standard_webhook_signature(body, headers, secret, *, now=None, tolerance_seconds=None):
    return _standard_webhook_signature_status(
        body,
        headers,
        secret,
        now=now,
        tolerance_seconds=tolerance_seconds,
    ) == "valid"


def _standard_webhook_signature_status(body, headers, secret, *, now=None, tolerance_seconds=None):
    webhook_id = _header_value(headers, _STANDARD_WEBHOOK_ID_HEADER)
    timestamp = _header_value(headers, _STANDARD_WEBHOOK_TIMESTAMP_HEADER)
    signature_header = _header_value(headers, _STANDARD_WEBHOOK_SIGNATURE_HEADER)
    if not (webhook_id and timestamp and signature_header):
        return "malformed"
    try:
        timestamp_int = int(timestamp)
    except (TypeError, ValueError):
        return "malformed"
    tolerance = _timestamp_tolerance_seconds() if tolerance_seconds is None else int(tolerance_seconds)
    current = int(time.time() if now is None else now)
    if abs(current - timestamp_int) > tolerance:
        return "stale"
    try:
        key = base64.b64decode(secret, validate=True)
    except (binascii.Error, ValueError):
        return "malformed"
    if len(key) != 32:
        return "malformed"
    signed_content = b".".join([
        webhook_id.encode("utf-8"),
        timestamp.encode("utf-8"),
        body,
    ])
    expected = base64.b64encode(
        hmac.new(key, signed_content, hashlib.sha256).digest()
    ).decode("ascii")
    matched = any(
        hmac.compare_digest(expected, signature)
        for version, signature in _standard_signature_entries(signature_header)
        if version == "v1" and signature
    )
    return "valid" if matched else "invalid"


def _standard_signature_entries(signature_header):
    # CA confirmed Standard-Webhooks format: `v1,<base64_hmac_sha256>`.
    # During rotation, multiple entries may be separated by whitespace; this
    # parser also tolerates comma-separated pairs without exposing values.
    parts = [part.strip() for part in re.split(r"[\s,]+", str(signature_header or "")) if part.strip()]
    for index in range(0, len(parts) - 1, 2):
        yield parts[index], parts[index + 1]


def _has_standard_webhook_headers(headers):
    return any(
        _header_value(headers, name)
        for name in (
            _STANDARD_WEBHOOK_ID_HEADER,
            _STANDARD_WEBHOOK_TIMESTAMP_HEADER,
            _STANDARD_WEBHOOK_SIGNATURE_HEADER,
        )
    )


def _header_value(headers, name):
    value = headers.get(name, "")
    return str(value).strip() if value is not None else ""


def _signature_status(body, headers):
    secret = os.environ.get("COMPLYADVANTAGE_WEBHOOK_SECRET", "")
    if secret:
        if _has_standard_webhook_headers(headers):
            return _standard_webhook_signature_status(body, headers, secret)
        return "valid" if _verify_signature(body, headers) else "invalid"
    if _environment() in ("staging", "production"):
        return "deployed_secret_missing"
    return "disabled_non_production"


def _standard_webhook_id(headers):
    return _header_value(headers, _STANDARD_WEBHOOK_ID_HEADER) if _has_standard_webhook_headers(headers) else None


def _timestamp_tolerance_seconds():
    raw = os.environ.get("COMPLYADVANTAGE_WEBHOOK_TOLERANCE_SECONDS", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TIMESTAMP_TOLERANCE_SECONDS
    return value if value > 0 else _DEFAULT_TIMESTAMP_TOLERANCE_SECONDS


def _environment():
    return os.environ.get("ENVIRONMENT", "development").strip().lower()


def _metric_signature_mode(signature_status):
    return {
        "valid": "strict",
        "invalid": "strict",
        "malformed": "strict",
        "stale": "strict",
        "deployed_secret_missing": "deployed_fail_closed",
        "disabled_non_production": "sandbox_fail_open",
    }.get(signature_status, "strict")


async def _call_storage_callback(callback, envelope, trace_id, webhook_id=None):
    kwargs = {}
    if accepts_keyword(callback, "trace_id"):
        kwargs["trace_id"] = trace_id
    if accepts_keyword(callback, "webhook_id"):
        kwargs["webhook_id"] = webhook_id
    if kwargs:
        return await callback(envelope, **kwargs)
    return await callback(envelope)


def _parse_known_envelope(event_type, payload):
    payload = {**payload, "webhook_type": event_type}
    if event_type == "CASE_CREATED":
        return CACaseCreatedWebhook.model_validate(payload)
    return CACaseAlertListUpdatedWebhook.model_validate(payload)


def _validate_known_payload(event_type, payload):
    extract_case_identifier(payload)
    if event_type == "CASE_ALERT_LIST_UPDATED":
        validate_alert_identifiers(payload)
