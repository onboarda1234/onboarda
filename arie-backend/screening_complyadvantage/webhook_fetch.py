"""Fetch-back service for ComplyAdvantage webhook resnapshots."""

import logging
import time
from datetime import datetime, timezone

from .client import ComplyAdvantageClient
from .config import CAConfig
from .models import CAAlertResponse, CACustomerInput, CACustomerResponse, CAWorkflowResponse
from .normalizer import ResnapshotContext, ScreeningApplicationContext, normalize_single_pass
from .orchestrator import (
    _extract_customer_identifier,
    _extract_risk_id,
    _normalise_next_link,
    _normalise_risk_as_alert,
    _parse_risk_detail,
)
from .observability import emit_metric, emit_operational, endpoint_category, status_family

logger = logging.getLogger(__name__)

_MAX_API_CALLS_PER_WEBHOOK = 200
_MAX_PAGES_PER_RESOURCE = 50


def build_default_client():
    """Lazily construct the production CA client after webhook validation."""
    return ComplyAdvantageClient(CAConfig.from_env())


def fetch_webhook_single_pass(client, envelope, application_context, trace_id=None):
    """Fetch current CA workflow/alert/risk state and return a normalized report."""
    case_identifier = extract_case_identifier(envelope)
    guard = _WebhookFetchGuard(client, trace_id=trace_id, webhook_type=getattr(envelope, "webhook_type", "none"))
    case_raw = fetch_case_for_webhook(case_identifier, guard)
    alerts = []
    deep_risks = {}
    alert_risk_listings = {}
    truncation = guard.truncation

    if getattr(envelope, "webhook_type", None) == "CASE_ALERT_LIST_UPDATED":
        alert_identifiers = validate_alert_identifiers(envelope)
        alerts, deep_risks, alert_risk_listings = _fetch_alert_event_enrichment(guard, alert_identifiers)
        truncation = guard.truncation

    workflow_raw = _case_backed_workflow_compat(case_identifier, case_raw, envelope)
    workflow = CAWorkflowResponse.model_validate(workflow_raw)
    customer_input = _customer_input_from_workflow_or_context(workflow_raw, application_context)
    customer_response = CACustomerResponse.model_validate({
        "identifier": _customer_identifier_from_envelope_or_workflow(envelope, workflow_raw),
        "external_identifier": getattr(envelope.customer, "external_identifier", None),
        "version": getattr(envelope.customer, "version", None),
    })
    resnapshot_context = ResnapshotContext(
        webhook_type=envelope.webhook_type,
        source_case_identifier=case_identifier,
        received_at=datetime.now(timezone.utc).isoformat(),
    )
    report = normalize_single_pass(
        workflow,
        alerts,
        deep_risks,
        customer_input,
        customer_response,
        application_context,
        resnapshot_context,
    )
    provider_specific = report.setdefault("provider_specific", {}).setdefault("complyadvantage", {})
    provider_specific["alert_risk_listings"] = alert_risk_listings
    if truncation:
        provider_specific["webhook_enrichment_truncated"] = truncation
    return report


class WebhookEnvelopeError(ValueError):
    """Raised when a CA webhook envelope does not carry a valid top-level case identifier."""


def extract_case_identifier(webhook_body_or_envelope):
    """Extract only the top-level CA case_identifier from a raw dict or parsed envelope."""
    if isinstance(webhook_body_or_envelope, dict):
        if "case_identifier" not in webhook_body_or_envelope:
            raise WebhookEnvelopeError("case_identifier missing")
        case_identifier = webhook_body_or_envelope.get("case_identifier")
    else:
        case_identifier = getattr(webhook_body_or_envelope, "case_identifier", None)
    if not isinstance(case_identifier, str):
        raise WebhookEnvelopeError("case_identifier must be a string")
    if len(case_identifier) != 36:
        raise WebhookEnvelopeError("case_identifier must be 36 characters")
    return case_identifier


def fetch_case_for_webhook(case_identifier: str, client):
    """Fetch current CA case detail using the verified webhook enrichment anchor."""
    return client.get(f"/v2/cases/{case_identifier}")


def validate_alert_identifiers(webhook_body_or_envelope):
    if isinstance(webhook_body_or_envelope, dict):
        if "alert_identifiers" not in webhook_body_or_envelope:
            raise WebhookEnvelopeError("alert_identifiers missing")
        alert_identifiers = webhook_body_or_envelope.get("alert_identifiers")
    else:
        if not hasattr(webhook_body_or_envelope, "alert_identifiers"):
            raise WebhookEnvelopeError("alert_identifiers missing")
        alert_identifiers = getattr(webhook_body_or_envelope, "alert_identifiers")
    if not isinstance(alert_identifiers, list):
        raise WebhookEnvelopeError("alert_identifiers must be a list")
    if any(not isinstance(alert_id, str) for alert_id in alert_identifiers):
        raise WebhookEnvelopeError("alert_identifiers must contain only strings")
    return list(alert_identifiers)


def _fetch_alert_event_enrichment(guard, alert_identifiers):
    alerts = []
    deep_risks = {}
    alert_risk_listings = {}
    for alert_id in alert_identifiers:
        for listing in _fetch_risk_listings_for_alert(guard, alert_id):
            if guard.truncation:
                break
            risk_id = _extract_risk_id(listing)
            alert_risk_listings[risk_id] = listing
            alerts.append(CAAlertResponse.model_validate(_normalise_risk_as_alert(risk_id, listing)))
            deep_raw = guard.get(f"/v2/entity-screening/risks/{risk_id}", resource="deep_risk", identifier=risk_id)
            if deep_raw is None:
                break
            _warn_nested_pagination(risk_id, deep_raw)
            deep_risks[risk_id] = _parse_deep_risk_preserving_payloads(deep_raw, listing)
        if guard.truncation:
            break
    return alerts, deep_risks, alert_risk_listings


def _fetch_risk_listings_for_alert(guard, alert_id):
    path = f"/v2/alerts/{alert_id}/risks"
    page_count = 0
    while path:
        if page_count >= _MAX_PAGES_PER_RESOURCE:
            logger.warning(
                "ca_webhook_fetch_page_cap_reached alert_id=%s page_cap=%d next_present=true",
                alert_id,
                _MAX_PAGES_PER_RESOURCE,
            )
            emit_metric(
                "webhook_fetch_page_cap_reached",
                metric_name="WebhookFetchPageCapReached",
                trace_id=getattr(guard, "trace_id", None),
                component="webhook_fetch",
                outcome="truncated",
                endpoint_category="alert_risks",
                alert_id=alert_id,
                page_cap=_MAX_PAGES_PER_RESOURCE,
            )
            emit_operational(
                "ca_webhook_fetch_truncated",
                trace_id=getattr(guard, "trace_id", None),
                component="webhook_fetch",
                outcome="truncated",
                endpoint_category="alert_risks",
                alert_id=alert_id,
                page_cap=_MAX_PAGES_PER_RESOURCE,
                truncation_reason="page_cap",
            )
            guard.mark_truncated("page_cap", alert_id=alert_id, page_cap=_MAX_PAGES_PER_RESOURCE)
            break
        page_count += 1
        raw = guard.get(path, resource="alert_risks", identifier=alert_id, page=page_count)
        if raw is None:
            break
        # Sandbox-confirmed CA shape: /v2/alerts/{alert_id}/risks uses top-level risks + next,
        # not the inner values + pagination.next envelope used inside deep-risk resources.
        for listing in raw.get("risks", []):
            yield listing
        next_link = raw.get("next")
        path = _normalise_next_link(getattr(guard, "client", guard), next_link)


def _parse_deep_risk_preserving_payloads(deep_raw, listing_raw):
    risk = _parse_risk_detail(deep_raw)
    extras = {key: value for key, value in deep_raw.items() if key != "values"}
    extras["alert_risk_listing"] = listing_raw
    existing = getattr(risk, "__pydantic_extra__", None) or {}
    existing.update(extras)
    object.__setattr__(risk, "__pydantic_extra__", existing)
    return risk


def _warn_nested_pagination(risk_id, value, path="detail"):
    if isinstance(value, dict):
        pagination = value.get("pagination")
        if isinstance(pagination, dict) and pagination.get("next"):
            logger.warning(
                "ca_webhook_nested_pagination risk_id=%s path=%s next_present=true",
                risk_id,
                path,
            )
            emit_metric(
                "webhook_fetch_nested_pagination_detected",
                metric_name="WebhookFetchNestedPaginationDetected",
                component="webhook_fetch",
                outcome="truncated",
                endpoint_category="deep_risk",
                risk_id=risk_id,
            )
        for key, item in value.items():
            _warn_nested_pagination(risk_id, item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _warn_nested_pagination(risk_id, item, f"{path}.{index}")


class _WebhookFetchGuard:
    """C4 webhook-level guardrails.

    The shared C2 client owns per-call timeouts and one auth refresh only; ordinary
    CA GET/POST 429/5xx retries are a separate C2 follow-up, not added here.
    """

    def __init__(self, client, max_calls=_MAX_API_CALLS_PER_WEBHOOK, trace_id=None, webhook_type="none"):
        self.client = client
        self.max_calls = max_calls
        self.calls = 0
        self.truncation = None
        self.trace_id = trace_id
        self.webhook_type = webhook_type

    def get(self, path, *, resource=None, identifier=None, page=None):
        category = endpoint_category(path)
        if self.calls >= self.max_calls:
            self.mark_truncated("api_call_budget", path=path)
            logger.error(
                "ca_webhook_fetch_api_call_budget_exceeded max_calls=%d attempted_path=%s resource=%s identifier=%s",
                self.max_calls,
                path,
                resource,
                identifier,
            )
            emit_metric(
                "webhook_fetch_api_call_budget_exhausted",
                metric_name="WebhookFetchApiCallBudgetExhausted",
                trace_id=self.trace_id,
                component="webhook_fetch",
                outcome="truncated",
                webhook_type=self.webhook_type,
                endpoint_category=category,
                api_call_budget=self.max_calls,
                path_template=path,
            )
            return None
        self.calls += 1
        logger.info(
            "ca_webhook_fetch_call method=GET path=%s call_number=%d max_calls=%d resource=%s identifier=%s page=%s",
            path,
            self.calls,
            self.max_calls,
            resource,
            identifier,
            page,
        )
        started = time.monotonic()
        try:
            result = self.client.get(path)
        except Exception as exc:
            family = status_family(error=exc)
            emit_metric(
                _failure_metric_for_category(category),
                trace_id=self.trace_id,
                component="webhook_fetch",
                outcome="failure",
                endpoint_category=category,
                status_family=family,
                api_call_number=self.calls,
                api_call_budget=self.max_calls,
            )
            emit_metric(
                "webhook_fetch_api_call",
                metric_name="WebhookFetchApiCalls",
                trace_id=self.trace_id,
                component="webhook_fetch",
                outcome="failure",
                endpoint_category=category,
                status_family=family,
            )
            emit_operational(
                "ca_webhook_fetch_hop_failed",
                trace_id=self.trace_id,
                component="webhook_fetch",
                outcome="failure",
                endpoint_category=category,
                status_family=family,
                api_call_number=self.calls,
                api_call_budget=self.max_calls,
                exception_class=exc.__class__.__name__,
            )
            raise
        emit_metric(
            "webhook_fetch_api_call",
            metric_name="WebhookFetchApiCalls",
            trace_id=self.trace_id,
            component="webhook_fetch",
            outcome="success",
            endpoint_category=category,
            status_family="2xx",
        )
        emit_metric(
            "webhook_step_result",
            metric_name="WebhookStepLatencyMs",
            value=int((time.monotonic() - started) * 1000),
            unit="Milliseconds",
            trace_id=self.trace_id,
            component="webhook_fetch",
            outcome="success",
            step=_step_for_category(category),
        )
        return result

    def mark_truncated(self, reason, **fields):
        if self.truncation is None:
            self.truncation = {"reason": reason, "calls": self.calls, **fields}


def _failure_metric_for_category(category):
    return {
        "case": "CaseFetchFailures",
        "alert_risks": "AlertRisksFetchFailures",
        "deep_risk": "DeepRiskFetchFailures",
    }.get(category, "WebhookFetchFailures")


def _step_for_category(category):
    return {
        "case": "case_fetch",
        "alert_risks": "alert_risks_fetch",
        "deep_risk": "deep_risk_fetch",
        "workflow": "workflow_fetch",
    }.get(category, "fetch")


def _case_backed_workflow_compat(case_identifier, case_raw, envelope):
    alert_ids = _alert_ids_for_envelope_or_case(envelope, case_raw)
    case_stage = case_raw.get("case_stage") or getattr(envelope, "case_stage", None)
    if hasattr(case_stage, "model_dump"):
        case_stage = case_stage.model_dump(mode="json")
    stage_identifier = (case_stage or {}).get("identifier") or "case-detail"

    # Case-backed compatibility object: normalize_single_pass currently accepts
    # CAWorkflowResponse, so this preserves case truth without claiming that the
    # CA case_identifier is a workflow_instance_identifier.
    return {
        "workflow_instance_identifier": f"case-backed:{case_identifier}",
        "workflow_type": "case-backed-webhook-resnapshot",
        "steps": [stage_identifier],
        "status": "IN-PROGRESS",
        "step_details": {
            stage_identifier: {
                "step_identifier": stage_identifier,
                "status": "IN-PROGRESS",
            }
        },
        "alerts": [{"identifier": alert_id} for alert_id in alert_ids],
        "case_identifier": case_identifier,
        "case_state": case_raw.get("case_state") or getattr(envelope, "case_state", None),
        "case_stage": case_stage,
        "case_detail": case_raw,
    }


def _alert_ids_for_envelope_or_case(envelope, case_raw):
    envelope_alerts = getattr(envelope, "alert_identifiers", None) or []
    if envelope_alerts:
        return list(envelope_alerts)
    raw_alerts = case_raw.get("alerts") or []
    if isinstance(raw_alerts, dict):
        raw_alerts = raw_alerts.get("values") or []
    ids = []
    for alert in raw_alerts:
        if isinstance(alert, dict):
            alert_id = alert.get("identifier") or alert.get("id")
            if alert_id:
                ids.append(alert_id)
        elif isinstance(alert, str):
            ids.append(alert)
    return ids


def _customer_identifier_from_envelope_or_workflow(envelope, workflow_raw):
    customer = getattr(envelope, "customer", None)
    if customer is not None and getattr(customer, "identifier", None):
        return customer.identifier
    return _extract_customer_identifier(workflow_raw)


def _customer_input_from_workflow_or_context(workflow_raw, context: ScreeningApplicationContext):
    raw_customer = workflow_raw.get("customer_input") or workflow_raw.get("customer")
    if raw_customer:
        return CACustomerInput.model_validate(raw_customer)
    if context.screening_subject_kind == "entity":
        return CACustomerInput.model_validate({"company": {"name": context.screening_subject_name}})
    first, last = _split_name(context.screening_subject_name)
    return CACustomerInput.model_validate({
        "person": {
            "first_name": first,
            "last_name": last,
            "full_name": context.screening_subject_name,
        }
    })


def _split_name(name):
    parts = (name or "Unknown").strip().split()
    if not parts:
        return "Unknown", "Subject"
    if len(parts) == 1:
        return parts[0], "Subject"
    return parts[0], " ".join(parts[1:])
