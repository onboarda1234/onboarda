"""Fetch-back service for ComplyAdvantage webhook resnapshots."""

from datetime import datetime, timezone

from .client import ComplyAdvantageClient
from .config import CAConfig
from .models import CACustomerInput, CACustomerResponse, CAWorkflowResponse
from .normalizer import ResnapshotContext, ScreeningApplicationContext, normalize_single_pass
from .orchestrator import _extract_customer_identifier


def build_default_client():
    """Lazily construct the production CA client after webhook validation."""
    return ComplyAdvantageClient(CAConfig.from_env())


def fetch_webhook_single_pass(client, envelope, application_context):
    """Fetch current CA workflow/alert/risk state and return a normalized report."""
    case_identifier = extract_case_identifier(envelope)
    case_raw = fetch_case_for_webhook(case_identifier, client)
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
    return normalize_single_pass(
        workflow,
        [],
        {},
        customer_input,
        customer_response,
        application_context,
        resnapshot_context,
    )


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
