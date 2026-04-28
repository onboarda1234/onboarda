"""ComplyAdvantage Mesh workflow orchestration."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
import time
from urllib.parse import urlparse

from .exceptions import CATimeout, CAUnexpectedResponse
from .models import (
    CAAlertResponse,
    CACustomerInput,
    CACustomerResponse,
    CAProfile,
    CARiskDetail,
    CARiskDetailInner,
    CARiskType,
    CASanctionIndicator,
    CASanctionValue,
    CAWatchlistIndicator,
    CAWatchlistValue,
    CAPEPIndicator,
    CAPEPValue,
    CAMediaIndicator,
    CAMediaArticleValue,
    CAWorkflowResponse,
)
from .normalizer import normalize_two_pass_screening
from .payloads import build_create_and_screen_payload, monitoring_enabled_from_payload
from .subscriptions import seed_monitoring_subscription


logger = logging.getLogger(__name__)

_POLL_INITIAL_DELAY = 0.0
_POLL_MULTIPLIER = 1.6
_POLL_INTERVAL_CAP = 30.0
_POLL_TOTAL_TIMEOUT = 300.0


@dataclass
class _PassResult:
    workflow: CAWorkflowResponse
    alerts: list[CAAlertResponse]
    deep_risks: dict[str, CARiskDetail]
    customer_input: CACustomerInput
    customer_response: CACustomerResponse
    monitoring_enabled: bool


class ComplyAdvantageScreeningOrchestrator:
    """Run CA create-and-screen workflows and normalize the result."""

    def __init__(self, client, poll_timeout_seconds=_POLL_TOTAL_TIMEOUT, clock=None, sleep_fn=None):
        self.client = client
        self.poll_timeout_seconds = poll_timeout_seconds
        self.clock = clock or time.monotonic
        self.sleep_fn = sleep_fn or time.sleep

    def screen_customer_two_pass(
        self,
        *,
        strict_customer,
        relaxed_customer,
        application_context,
        monitoring_enabled=True,
        db=None,
        workflow_id=None,
        external_identifier=None,
    ):
        """Run strict and relaxed passes concurrently and return a normalized report dict."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            strict_future = executor.submit(
                self._run_one_pass,
                strict_customer,
                monitoring_enabled=monitoring_enabled,
                workflow_id=workflow_id,
                external_identifier=external_identifier,
            )
            relaxed_future = executor.submit(
                self._run_one_pass,
                relaxed_customer,
                monitoring_enabled=monitoring_enabled,
                workflow_id=workflow_id,
                external_identifier=external_identifier,
            )
            strict = strict_future.result()
            relaxed = relaxed_future.result()

        report = normalize_two_pass_screening(
            strict.workflow,
            strict.alerts,
            strict.deep_risks,
            relaxed.workflow,
            relaxed.alerts,
            relaxed.deep_risks,
            strict.customer_input,
            strict.customer_response,
            application_context,
        )
        self._seed_subscription_if_needed(strict, application_context, db)
        return report

    def create_and_screen(self, customer, *, monitoring_enabled=True, workflow_id=None, external_identifier=None):
        payload = build_create_and_screen_payload(
            customer,
            monitoring_enabled=monitoring_enabled,
            workflow_id=workflow_id,
            external_identifier=external_identifier,
        )
        raw = self.client.post("/v2/workflows/create-and-screen", json_body=payload)
        workflow_raw = raw.get("workflow") or raw.get("workflow_instance") or raw.get("workflow_response")
        customer_raw = raw.get("customer") or raw.get("customer_response")
        if workflow_raw is None:
            if "workflow_instance_identifier" in raw:
                workflow_raw = raw
            else:
                raise CAUnexpectedResponse("ComplyAdvantage create-and-screen workflow missing")
        if customer_raw is None:
            raise CAUnexpectedResponse("ComplyAdvantage create-and-screen customer missing")
        return (
            CAWorkflowResponse.model_validate(workflow_raw),
            CACustomerResponse.model_validate(customer_raw),
            CACustomerInput.model_validate(payload["customer"]),
            monitoring_enabled_from_payload(payload),
        )

    def poll_workflow_until_complete(self, workflow_id):
        deadline = self.clock() + float(self.poll_timeout_seconds)
        delay = _POLL_INITIAL_DELAY
        while True:
            if delay > 0:
                self.sleep_fn(delay)
            raw = self.client.get(f"/v2/workflows/{workflow_id}")
            workflow = CAWorkflowResponse.model_validate(raw)
            if self._workflow_complete(workflow):
                return workflow
            if self.clock() >= deadline:
                raise CATimeout("ComplyAdvantage workflow polling timed out")
            delay = 1.0 if delay <= 0 else min(delay * _POLL_MULTIPLIER, _POLL_INTERVAL_CAP)

    def fetch_alerts_paginated(self, workflow_id, *, page_size=25):
        path = f"/v2/workflows/{workflow_id}/alerts"
        params = {"page_size": page_size}
        alerts = []
        while path:
            raw = self.client.get(path, params=params)
            params = None
            values = raw.get("values", [])
            alerts.extend(CAAlertResponse.model_validate(_normalise_alert(v)) for v in values)
            next_link = (raw.get("pagination") or {}).get("next")
            path = self._normalise_next_link(next_link)
        return alerts

    def fetch_deep_risks_for_alert(self, alert):
        raw = self.client.get(f"/v2/entity-screening/risks/{alert.identifier}")
        try:
            return _parse_risk_detail(raw)
        except Exception as exc:
            raise CAUnexpectedResponse("ComplyAdvantage deep-risk response malformed") from exc

    def _run_one_pass(self, customer, *, monitoring_enabled, workflow_id, external_identifier):
        initial, customer_response, customer_input, enabled = self.create_and_screen(
            customer,
            monitoring_enabled=monitoring_enabled,
            workflow_id=workflow_id,
            external_identifier=external_identifier,
        )
        workflow = self.poll_workflow_until_complete(initial.workflow_instance_identifier)
        if self._case_creation_skipped(workflow):
            return _PassResult(workflow, [], {}, customer_input, customer_response, enabled)
        alerts = self.fetch_alerts_paginated(workflow.workflow_instance_identifier)
        deep_risks = {}
        for alert in alerts:
            deep_risks[alert.identifier] = self.fetch_deep_risks_for_alert(alert)
        return _PassResult(workflow, alerts, deep_risks, customer_input, customer_response, enabled)

    def _normalise_next_link(self, next_link):
        if not next_link:
            return None
        parsed = urlparse(next_link)
        if not parsed.scheme and not parsed.netloc:
            return next_link
        base = getattr(getattr(self.client, "config", None), "api_base_url", "")
        base_parsed = urlparse(base)
        if (parsed.scheme, parsed.netloc) != (base_parsed.scheme, base_parsed.netloc):
            raise CAUnexpectedResponse("ComplyAdvantage pagination next host unexpected")
        path = parsed.path or "/"
        return f"{path}?{parsed.query}" if parsed.query else path

    def _workflow_complete(self, workflow):
        status = _status_value(workflow.status)
        case_detail = workflow.step_details.get("case-creation")
        case_status = _status_value(case_detail.status) if case_detail else None
        if status == "COMPLETED" and case_status in (None, "COMPLETED", "SKIPPED"):
            return True
        if status == "IN-PROGRESS" or case_status == "IN-PROGRESS":
            return False
        raise CAUnexpectedResponse("ComplyAdvantage workflow status unexpected")

    def _case_creation_skipped(self, workflow):
        detail = workflow.step_details.get("case-creation")
        return detail is not None and _status_value(detail.status) == "SKIPPED"

    def _seed_subscription_if_needed(self, result, context, db):
        customer_identifier = result.customer_response.identifier
        if not (result.monitoring_enabled and customer_identifier):
            return
        workflow_id = result.workflow.workflow_instance_identifier
        if db is None:
            logger.warning(
                "ca_monitoring_subscription_skipped workflow_id=%s customer_identifier=%s reason=%s",
                workflow_id,
                customer_identifier,
                "db_handle_not_injected",
            )
            return
        seed_monitoring_subscription(
            db,
            context.client_id,
            context.application_id,
            customer_identifier,
            person_key=context.screening_subject_person_key,
        )
        logger.info(
            "ca_monitoring_subscription_seeded workflow_id=%s customer_identifier=%s",
            workflow_id,
            customer_identifier,
        )


def _status_value(value):
    return getattr(value, "value", value)


def _normalise_alert(raw):
    data = dict(raw)
    if "profile" in data and data["profile"] is not None:
        data["profile"] = CAProfile.model_validate(data["profile"]).model_dump(mode="json")
    data.setdefault("risk_details", {"values": []})
    return data


def _parse_risk_detail(raw):
    values = []
    for item in raw.get("values", []):
        risk_type = CARiskType.model_validate(item["risk_type"])
        indicators = []
        for indicator in item.get("indicators", []):
            indicators.append(_parse_indicator(risk_type, indicator.get("value", {})))
        values.append(CARiskDetailInner(risk_type=risk_type, indicators=indicators))
    return CARiskDetail(values=values)


def _parse_indicator(risk_type, value):
    key = risk_type.key
    if key.startswith("r_pep") or key == "r_rca":
        return CAPEPIndicator(risk_type=risk_type, value=CAPEPValue.model_validate(value))
    if key.startswith("r_adverse_media"):
        return CAMediaIndicator(risk_type=risk_type, value=CAMediaArticleValue.model_validate(value))
    if key.startswith("r_sanctions_exposure") or key in {"r_watchlist", "r_law_enforcement"}:
        return CAWatchlistIndicator(risk_type=risk_type, value=CAWatchlistValue.model_validate(value))
    return CASanctionIndicator(risk_type=risk_type, value=CASanctionValue.model_validate(value))
