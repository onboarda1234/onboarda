"""Fetch-back service for ComplyAdvantage webhook resnapshots."""

from datetime import datetime, timezone

from .client import ComplyAdvantageClient
from .config import CAConfig
from .models import CACustomerInput, CACustomerResponse, CAWorkflowResponse
from .normalizer import ResnapshotContext, ScreeningApplicationContext, normalize_single_pass
from .orchestrator import _extract_customer_identifier, _fetch_alerts_and_deep_risks


def build_default_client():
    """Lazily construct the production CA client after webhook validation."""
    return ComplyAdvantageClient(CAConfig.from_env())


def fetch_webhook_single_pass(client, envelope, application_context):
    """Fetch current CA workflow/alert/risk state and return a normalized report."""
    workflow_raw = client.get(f"/v2/workflows/{envelope.case_identifier}")
    workflow = CAWorkflowResponse.model_validate(workflow_raw)
    alert_ids = _alert_ids_for_envelope(envelope, workflow_raw)
    alerts, deep_risks = _fetch_alerts_and_deep_risks(client, workflow_raw, alert_ids=alert_ids)
    customer_input = _customer_input_from_workflow_or_context(workflow_raw, application_context)
    customer_response = CACustomerResponse.model_validate({
        "identifier": _customer_identifier_from_envelope_or_workflow(envelope, workflow_raw),
        "external_identifier": getattr(envelope.customer, "external_identifier", None),
        "version": getattr(envelope.customer, "version", None),
    })
    resnapshot_context = ResnapshotContext(
        webhook_type=envelope.webhook_type,
        source_case_identifier=envelope.case_identifier,
        received_at=datetime.now(timezone.utc).isoformat(),
    )
    return normalize_single_pass(
        workflow,
        alerts,
        deep_risks,
        customer_input,
        customer_response,
        application_context,
        resnapshot_context,
    )


def _alert_ids_for_envelope(envelope, workflow_raw):
    envelope_alerts = getattr(envelope, "alert_identifiers", None) or []
    if envelope_alerts:
        return list(envelope_alerts)
    return None


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
