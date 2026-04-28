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
_PEP_RISK_PREFIX = "r_pep"
_RCA_RISK_KEY = "r_rca"
_ADVERSE_MEDIA_RISK_PREFIX = "r_adverse_media"
_WATCHLIST_RISK_KEYS = frozenset({"r_watchlist", "r_law_enforcement"})
_SANCTIONS_EXPOSURE_PREFIX = "r_sanctions_exposure"


@dataclass
class _CreateAndScreenResult:
    workflow_instance_identifier: str
    customer_input: CACustomerInput
    monitoring_enabled: bool


@dataclass
class _WorkflowPollResult:
    workflow: CAWorkflowResponse
    raw: dict


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
        workflow_id = raw.get("workflow_instance_identifier")
        if not workflow_id:
            raise CAUnexpectedResponse("ComplyAdvantage create-and-screen workflow handle missing")
        return _CreateAndScreenResult(
            workflow_instance_identifier=workflow_id,
            customer_input=CACustomerInput.model_validate(payload["customer"]),
            monitoring_enabled=monitoring_enabled_from_payload(payload),
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
                return _WorkflowPollResult(workflow=workflow, raw=raw)
            if self.clock() >= deadline:
                raise CATimeout("ComplyAdvantage workflow polling timed out")
            delay = 1.0 if delay <= 0 else min(delay * _POLL_MULTIPLIER, _POLL_INTERVAL_CAP)

    def fetch_risks_paginated_for_alert(self, alert_id):
        path = f"/v2/alerts/{alert_id}/risks?page=1"
        risks = []
        while path:
            raw = self.client.get(path)
            risks.extend(raw.get("values", []))
            next_link = (raw.get("pagination") or {}).get("next")
            path = self._normalise_next_link(next_link)
        return risks

    def fetch_deep_risk(self, risk_id):
        raw = self.client.get(f"/v2/entity-screening/risks/{risk_id}")
        try:
            return _parse_risk_detail(raw)
        except Exception as exc:
            raise CAUnexpectedResponse("ComplyAdvantage deep-risk response malformed") from exc

    def _run_one_pass(self, customer, *, monitoring_enabled, workflow_id, external_identifier):
        initial = self.create_and_screen(
            customer,
            monitoring_enabled=monitoring_enabled,
            workflow_id=workflow_id,
            external_identifier=external_identifier,
        )
        polled = self.poll_workflow_until_complete(initial.workflow_instance_identifier)
        workflow = polled.workflow
        customer_identifier = _extract_customer_identifier(polled.raw)
        customer_response = CACustomerResponse.model_validate({"identifier": customer_identifier})
        if self._case_creation_skipped(workflow):
            return _PassResult(workflow, [], {}, initial.customer_input, customer_response, initial.monitoring_enabled)
        alert_ids = _extract_alert_ids(polled.raw)
        alerts = []
        deep_risks = {}
        for alert_id in alert_ids:
            for risk in self.fetch_risks_paginated_for_alert(alert_id):
                risk_id = _extract_risk_id(risk)
                alerts.append(CAAlertResponse.model_validate(_normalise_risk_as_alert(risk_id, risk)))
                deep_risks[risk_id] = self.fetch_deep_risk(risk_id)
        return _PassResult(workflow, alerts, deep_risks, initial.customer_input, customer_response, initial.monitoring_enabled)

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


def _normalise_risk_as_alert(risk_id, raw):
    data = {"identifier": risk_id}
    if "profile" in data and data["profile"] is not None:
        data["profile"] = CAProfile.model_validate(data["profile"]).model_dump(mode="json")
    elif raw.get("profile") is not None:
        data["profile"] = CAProfile.model_validate(raw["profile"]).model_dump(mode="json")
    data.setdefault("risk_details", {"values": []})
    return data


def _extract_alert_ids(workflow_raw):
    alerts = workflow_raw.get("alerts") or []
    ids = [_extract_identifier(item) for item in alerts]
    return [value for value in ids if value]


def _extract_risk_id(risk_raw):
    risk_id = _extract_identifier(risk_raw) or risk_raw.get("risk_id")
    if not risk_id:
        raise CAUnexpectedResponse("ComplyAdvantage risk identifier missing")
    return risk_id


def _extract_identifier(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("identifier") or value.get("id")
    return None


def _extract_customer_identifier(workflow_raw):
    step_details = workflow_raw.get("step_details") or {}
    customer_creation = step_details.get("customer-creation") or {}
    output = customer_creation.get("output") or {}
    customer_identifier = output.get("customer_identifier") or output.get("identifier")
    if not customer_identifier:
        customer_identifier = workflow_raw.get("customer_identifier")
    if not customer_identifier:
        raise CAUnexpectedResponse("ComplyAdvantage workflow customer identifier missing")
    return customer_identifier


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
    if key.startswith(_PEP_RISK_PREFIX) or key == _RCA_RISK_KEY:
        return CAPEPIndicator(risk_type=risk_type, value=CAPEPValue.model_validate(value))
    if key.startswith(_ADVERSE_MEDIA_RISK_PREFIX):
        return CAMediaIndicator(risk_type=risk_type, value=CAMediaArticleValue.model_validate(value))
    if key.startswith(_SANCTIONS_EXPOSURE_PREFIX) or key in _WATCHLIST_RISK_KEYS:
        return CAWatchlistIndicator(risk_type=risk_type, value=CAWatchlistValue.model_validate(value))
    return CASanctionIndicator(risk_type=risk_type, value=CASanctionValue.model_validate(value))
