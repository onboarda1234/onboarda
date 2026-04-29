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
        if not _verify_signature(body, signature):
            logger.warning(
                "ca_webhook_signature signature_invalid=true body_len=%d signature_present=%s",
                len(body),
                bool(signature),
            )
            self.set_status(401)
            return
        logger.info("ca_webhook_signature signature_valid=true body_len=%d", len(body))

        try:
            payload = json.loads(body)
        except Exception:
            logger.warning("ca_webhook_invalid_json body_len=%d", len(body))
            self.set_status(400)
            return

        event_type = payload.get("webhook_type") or payload.get("type") or ""
        if event_type not in _KNOWN_TYPES:
            envelope = CAUnknownWebhookEnvelope.model_validate({**payload, "webhook_type": event_type})
            customer = getattr(envelope, "customer", None)
            logger.info(
                "ca_webhook_unknown_event event_type=%s case_identifier=%s customer_identifier=%s",
                envelope.webhook_type,
                envelope.case_identifier,
                getattr(customer, "identifier", None),
            )
            self.set_status(202)
            return

        try:
            envelope = _parse_known_envelope(event_type, payload)
        except ValidationError:
            logger.warning("ca_webhook_envelope_invalid event_type=%s", event_type, exc_info=True)
            self.set_status(202)
            return

        self.set_status(202)
        tornado.ioloop.IOLoop.current().spawn_callback(self._process_webhook_async, envelope)

    async def _process_webhook_async(self, envelope):
        try:
            await self._storage_callback(envelope)
        except Exception:
            from .webhook_storage import emit_metric
            logger.error(
                "ca_webhook_async_processing_failure webhook_type=%s case_identifier=%s",
                getattr(envelope, "webhook_type", None),
                getattr(envelope, "case_identifier", None),
                exc_info=True,
            )
            emit_metric("webhook_async_processing_failure", provider=COMPLYADVANTAGE_PROVIDER_NAME)


def _verify_signature(body, signature):
    secret = os.environ.get("COMPLYADVANTAGE_WEBHOOK_SECRET", "")
    if not secret or not isinstance(secret, str):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _parse_known_envelope(event_type, payload):
    payload = {**payload, "webhook_type": event_type}
    if event_type == "CASE_CREATED":
        return CACaseCreatedWebhook.model_validate(payload)
    return CACaseAlertListUpdatedWebhook.model_validate(payload)
