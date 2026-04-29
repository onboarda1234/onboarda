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
    raise FetchBackAnchorUnresolved(
        "C4 fetch-back anchor unresolved: locked design says webhooks provide "
        "case_identifier and exact CA fetch-back endpoints need Step 2 sandbox "
        "confirmation; repo fixtures do not prove that case_identifier is a "
        "workflow_instance_identifier."
    )


class FetchBackAnchorUnresolved(RuntimeError):
    """Raised when repo evidence is insufficient to choose a safe CA fetch-back endpoint."""


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
