"""Tornado handler for ComplyAdvantage webhooks.

The receiver uses the C4 spawn-callback hybrid: it reads the raw body first,
verifies the HMAC synchronously, parses the envelope, returns HTTP 202 with an
empty body for known events, and only then schedules the heavier fetch-back and
dual-write sequence with ``IOLoop.spawn_callback``. This keeps ComplyAdvantage
from timing out on long fetch-back work, but work can be lost if the process dies
after the 202 and before/during the callback. The v1 mitigation is natural DB
deduplication: ``screening_reports_normalized`` is unique by provider/hash and
``monitoring_alerts`` is unique by provider/case_identifier; future reconcile
jobs can repair inconsistent state.
"""

import hashlib
import hmac
import json
import logging
import os

import tornado.ioloop
from pydantic import ValidationError

from base_handler import BaseHandler
from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME

from .models.webhooks import CACaseAlertListUpdatedWebhook, CACaseCreatedWebhook, CAUnknownWebhookEnvelope
from .observability import accepts_keyword, emit_metric, emit_operational, inbound_trace_id
from .webhook_fetch import WebhookEnvelopeError, extract_case_identifier, validate_alert_identifiers
from .webhook_storage import process_complyadvantage_webhook

logger = logging.getLogger(__name__)

_SIGNATURE_HEADER = "x-complyadvantage-signature"
_KNOWN_TYPES = {"CASE_CREATED", "CASE_ALERT_LIST_UPDATED"}


class ComplyAdvantageWebhookHandler(BaseHandler):
    """POST /api/webhooks/complyadvantage."""

    def initialize(self, storage_callback=None):
        self._storage_callback = storage_callback or process_complyadvantage_webhook

    def post(self):
        body = self.request.body
        signature = self.request.headers.get(_SIGNATURE_HEADER, "")
        trace_id = inbound_trace_id(self.request.headers.get("X-Request-ID"))
        signature_status = _signature_status(body, signature)
        if signature_status == "invalid":
            logger.warning(
                "ca_webhook_signature signature_mode=strict signature_invalid=true body_len=%d",
                len(body),
            )
            emit_metric(
                "webhook_signature_failure",
                metric_name="WebhookSignatureFailures",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                signature_mode="strict",
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
        if signature_status == "production_secret_missing":
            logger.error(
                "ca_webhook_signature signature_mode=production_fail_closed signature_secret_configured=false"
            )
            emit_metric(
                "webhook_signature_failure",
                metric_name="WebhookSignatureFailures",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                signature_mode="production_fail_closed",
            )
            emit_metric(
                "env_mode_drift",
                metric_name="EnvModeDrift",
                trace_id=trace_id,
                component="webhook_handler",
                outcome="failure",
                signature_mode="production_fail_closed",
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
        tornado.ioloop.IOLoop.current().spawn_callback(self._process_webhook_async, envelope, trace_id=trace_id)

    async def _process_webhook_async(self, envelope, trace_id=None):
        try:
            await _call_storage_callback(self._storage_callback, envelope, trace_id)
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


def _verify_signature(body, signature):
    secret = os.environ.get("COMPLYADVANTAGE_WEBHOOK_SECRET", "")
    if not secret or not isinstance(secret, str):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _signature_status(body, signature):
    secret = os.environ.get("COMPLYADVANTAGE_WEBHOOK_SECRET", "")
    if secret:
        return "valid" if _verify_signature(body, signature) else "invalid"
    if _environment() == "production":
        return "production_secret_missing"
    return "disabled_non_production"


def _environment():
    return os.environ.get("ENVIRONMENT", "development").strip().lower()


def _metric_signature_mode(signature_status):
    return {
        "valid": "strict",
        "invalid": "strict",
        "production_secret_missing": "production_fail_closed",
        "disabled_non_production": "sandbox_fail_open",
    }.get(signature_status, "strict")


async def _call_storage_callback(callback, envelope, trace_id):
    if accepts_keyword(callback, "trace_id"):
        return await callback(envelope, trace_id=trace_id)
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
