"""ComplyAdvantage Mesh workflow orchestration."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import logging
import re
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
from .observability import emit_metric
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
_PEP_AML_TYPE_TO_CLASS = {
    "pep-class-1": ("r_pep_class_1", "PEP Class 1", "PEP_CLASS_1"),
    "pep-class-2": ("r_pep_class_2", "PEP Class 2", "PEP_CLASS_2"),
    "pep-class-3": ("r_pep_class_3", "PEP Class 3", "PEP_CLASS_3"),
    "pep-class-4": ("r_pep_class_4", "PEP Class 4", "PEP_CLASS_4"),
}
_PEP_RISK_KEY_TO_CLASS = {
    "r_pep_class_1": ("PEP Class 1", "PEP_CLASS_1"),
    "r_pep_class_2": ("PEP Class 2", "PEP_CLASS_2"),
    "r_pep_class_3": ("PEP Class 3", "PEP_CLASS_3"),
    "r_pep_class_4": ("PEP Class 4", "PEP_CLASS_4"),
}


@dataclass
class _CreateAndScreenResult:
    workflow_instance_identifier: str
    customer_input: CACustomerInput
    monitoring_enabled: bool


@dataclass
class _WorkflowPollResult:
    workflow: CAWorkflowResponse
    raw: dict
    timed_out: bool = False
    errored: bool = False


@dataclass
class _PassResult:
    workflow: CAWorkflowResponse
    alerts: list[CAAlertResponse]
    deep_risks: dict[str, CARiskDetail]
    customer_input: CACustomerInput
    customer_response: CACustomerResponse
    monitoring_enabled: bool
    timed_out: bool = False
    errored: bool = False
    identifier_conflict: bool = False


class ComplyAdvantageScreeningOrchestrator:
    """Run CA create-and-screen workflows and normalize the result."""

    def __init__(
        self,
        client,
        poll_timeout_seconds=_POLL_TOTAL_TIMEOUT,
        clock=None,
        sleep_fn=None,
        allow_pending_on_timeout=False,
    ):
        self.client = client
        self.poll_timeout_seconds = poll_timeout_seconds
        self.clock = clock or time.monotonic
        self.sleep_fn = sleep_fn or time.sleep
        self.allow_pending_on_timeout = bool(allow_pending_on_timeout)

    def screen_customer_two_pass(
        self,
        *,
        strict_customer,
        relaxed_customer,
        application_context,
        monitoring_enabled=True,
        db=None,
        screening_configuration_identifier=None,
        external_identifier=None,
        strict_external_identifier=None,
        relaxed_external_identifier=None,
    ):
        """Run strict and relaxed passes concurrently and return a normalized report dict."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            strict_future = executor.submit(
                self._run_one_pass,
                strict_customer,
                monitoring_enabled=monitoring_enabled,
                screening_configuration_identifier=screening_configuration_identifier,
                external_identifier=strict_external_identifier or external_identifier,
            )
            relaxed_future = executor.submit(
                self._run_one_pass,
                relaxed_customer,
                monitoring_enabled=monitoring_enabled,
                screening_configuration_identifier=screening_configuration_identifier,
                external_identifier=relaxed_external_identifier or external_identifier,
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
        if strict.timed_out or relaxed.timed_out:
            _mark_report_pending_after_timeout(report, strict=strict, relaxed=relaxed)
        if strict.errored or relaxed.errored:
            _mark_report_errored(report, strict=strict, relaxed=relaxed)
        if strict.identifier_conflict or relaxed.identifier_conflict:
            _mark_report_customer_conflict(report)
        self._seed_subscription_if_needed(strict, application_context, db)
        return report

    def create_and_screen(
        self,
        customer,
        *,
        monitoring_enabled=True,
        screening_configuration_identifier=None,
        external_identifier=None,
    ):
        payload = build_create_and_screen_payload(
            customer,
            monitoring_enabled=monitoring_enabled,
            screening_configuration_identifier=screening_configuration_identifier,
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
        return _poll_workflow_until_complete(
            self.client,
            workflow_id,
            poll_timeout_seconds=self.poll_timeout_seconds,
            clock=self.clock,
            sleep_fn=self.sleep_fn,
            allow_pending_on_timeout=self.allow_pending_on_timeout,
        )

    def fetch_risks_paginated_for_alert(self, alert_id):
        return _fetch_risks_paginated_for_alert(self.client, alert_id)

    def fetch_deep_risk(self, risk_id):
        return _fetch_deep_risk(self.client, risk_id)

    def _run_one_pass(
        self,
        customer,
        *,
        monitoring_enabled,
        screening_configuration_identifier,
        external_identifier,
    ):
        initial = self.create_and_screen(
            customer,
            monitoring_enabled=monitoring_enabled,
            screening_configuration_identifier=screening_configuration_identifier,
            external_identifier=external_identifier,
        )
        polled = self.poll_workflow_until_complete(initial.workflow_instance_identifier)
        workflow = polled.workflow
        degraded = polled.timed_out or polled.errored or self._case_creation_skipped(workflow)
        customer_identifier = _extract_customer_identifier(polled.raw, required=not degraded)
        customer_response = CACustomerResponse.model_validate({"identifier": customer_identifier})
        if degraded:
            return _PassResult(
                workflow,
                [],
                {},
                initial.customer_input,
                customer_response,
                initial.monitoring_enabled,
                timed_out=polled.timed_out,
                errored=polled.errored,
                identifier_conflict=polled.errored and _customer_identifier_conflict(polled.raw),
            )
        alerts, deep_risks = _fetch_alerts_and_deep_risks(self.client, polled.raw)
        return _PassResult(workflow, alerts, deep_risks, initial.customer_input, customer_response, initial.monitoring_enabled)

    def _normalise_next_link(self, next_link):
        return _normalise_next_link(self.client, next_link)

    def _workflow_complete(self, workflow):
        return _workflow_complete(workflow)

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
            emit_metric(
                "monitoring_subscription_skipped",
                metric_name="MonitoringSubscriptionSkipped",
                component="orchestrator",
                outcome="skipped",
                step="subscription_seed",
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
        emit_metric(
            "monitoring_subscription_seeded",
            metric_name="MonitoringSubscriptionSeeded",
            component="orchestrator",
            outcome="success",
            step="subscription_seed",
        )


def _status_value(value):
    return getattr(value, "value", value)


def _poll_workflow_until_complete(
    client,
    workflow_id,
    *,
    poll_timeout_seconds=_POLL_TOTAL_TIMEOUT,
    clock=None,
    sleep_fn=None,
    allow_pending_on_timeout=False,
):
    clock = clock or time.monotonic
    sleep_fn = sleep_fn or time.sleep
    deadline = clock() + float(poll_timeout_seconds)
    delay = _POLL_INITIAL_DELAY
    last_raw = None
    while True:
        if delay > 0:
            remaining = deadline - clock()
            if remaining <= 0:
                if allow_pending_on_timeout and last_raw:
                    return _pending_timeout_poll_result(
                        workflow_id=workflow_id,
                        poll_timeout_seconds=poll_timeout_seconds,
                        raw=last_raw,
                    )
                raise CATimeout("ComplyAdvantage workflow polling timed out")
            sleep_fn(min(delay, remaining))
        poll_started = clock()
        raw = client.get(f"/v2/workflows/{workflow_id}")
        last_raw = raw
        emit_metric(
            "workflow_poll_attempt",
            metric_name="WorkflowPollingAttempts",
            component="orchestrator",
            outcome="success",
            step="workflow_poll",
        )
        emit_metric(
            "workflow_poll_latency",
            metric_name="WorkflowPollingLatencyMs",
            value=int((clock() - poll_started) * 1000),
            unit="Milliseconds",
            component="orchestrator",
            outcome="success",
            step="workflow_poll",
        )
        workflow = CAWorkflowResponse.model_validate(raw)
        if _workflow_errored(workflow):
            return _WorkflowPollResult(workflow=workflow, raw=raw, errored=True)
        if _workflow_complete(workflow):
            return _WorkflowPollResult(workflow=workflow, raw=raw)
        if clock() >= deadline:
            if allow_pending_on_timeout and last_raw:
                return _pending_timeout_poll_result(
                    workflow_id,
                    poll_timeout_seconds,
                    last_raw,
                    workflow=workflow,
                )
            raise CATimeout("ComplyAdvantage workflow polling timed out")
        delay = 1.0 if delay <= 0 else min(delay * _POLL_MULTIPLIER, _POLL_INTERVAL_CAP)


def _pending_timeout_poll_result(
    workflow_id,
    poll_timeout_seconds,
    raw,
    *,
    workflow=None,
):
    if workflow is None:
        workflow = CAWorkflowResponse.model_validate(raw)
    logger.warning(
        "ca_workflow_poll_pending_timeout workflow_id=%s timeout_seconds=%s status=%s",
        workflow_id,
        poll_timeout_seconds,
        getattr(workflow.status, "value", workflow.status),
    )
    emit_metric(
        "workflow_poll_pending_timeout",
        metric_name="WorkflowPollingPendingTimeouts",
        component="orchestrator",
        outcome="timeout",
        step="workflow_poll",
    )
    raw = dict(raw)
    raw["_complyadvantage_pending_timeout"] = True
    return _WorkflowPollResult(workflow=workflow, raw=raw, timed_out=True)


def _mark_report_pending_after_timeout(report, *, strict, relaxed):
    _mark_report_degraded(
        report,
        strict=strict,
        relaxed=relaxed,
        provider_flag_key="pending_timeout",
        provider_ids_key="pending_timeout_workflow_ids",
        degraded_source="complyadvantage_workflow_pending",
        overall_flag="ComplyAdvantage screening is still processing; live terminal screening is required before approval.",
        pending_reason="workflow_poll_timeout",
    )


def _mark_report_errored(report, *, strict, relaxed):
    _mark_report_degraded(
        report,
        strict=strict,
        relaxed=relaxed,
        provider_flag_key="workflow_errored",
        provider_ids_key="errored_workflow_ids",
        degraded_source="complyadvantage_workflow_errored",
        overall_flag="ComplyAdvantage screening workflow errored; a live terminal screening is required before approval.",
        pending_reason="workflow_errored",
    )


def _mark_report_degraded(
    report,
    *,
    strict,
    relaxed,
    provider_flag_key,
    provider_ids_key,
    degraded_source,
    overall_flag,
    pending_reason,
):
    """Mark a screening report as non-terminal/degraded (blocks approval, needs
    re-screen). Shared by the poll-timeout and workflow-errored paths — both
    leave screening in the recognised ``pending_provider`` state so downstream
    approval gates treat them identically; only the reason/flag/markers differ.
    """
    provider = (report.get("provider_specific") or {}).get("complyadvantage")
    if isinstance(provider, dict):
        provider[provider_flag_key] = True
        provider[provider_ids_key] = [
            workflow_id
            for workflow_id in (
                getattr(strict.workflow, "workflow_instance_identifier", None),
                getattr(relaxed.workflow, "workflow_instance_identifier", None),
            )
            if workflow_id
        ]
    report["any_non_terminal_subject"] = True
    report["degraded_sources"] = list(dict.fromkeys(
        list(report.get("degraded_sources") or []) + [degraded_source]
    ))
    flags = list(report.get("overall_flags") or [])
    if overall_flag not in flags:
        flags.append(overall_flag)
    report["overall_flags"] = flags
    report["company_screening_state"] = "pending_provider"
    company = report.get("company_screening")
    if isinstance(company, dict):
        company["api_status"] = "pending"
        company["screening_state"] = "pending_provider"
        if "matched" not in company:
            company["matched"] = False
        company.setdefault("results", [])
        company["pending_reason"] = pending_reason
    for group_name in ("director_screenings", "ubo_screenings", "intermediary_screenings"):
        for subject in report.get(group_name) or []:
            if not isinstance(subject, dict):
                continue
            subject["screening_state"] = "pending_provider"
            subject["requires_review"] = True
            screening = subject.get("screening")
            if not isinstance(screening, dict):
                screening = {}
                subject["screening"] = screening
            screening["api_status"] = "pending"
            screening["source"] = "complyadvantage"
            screening["provider"] = "complyadvantage"
            screening["pending_reason"] = pending_reason


def _fetch_risks_paginated_for_alert(client, alert_id):
    path = f"/v2/alerts/{alert_id}/risks?page=1"
    risks = []
    while path:
        raw = client.get(path)
        # Sandbox-confirmed CA shape: /v2/alerts/{alert_id}/risks uses top-level risks + next,
        # not the inner values + pagination.next envelope used inside deep-risk resources.
        risks.extend(raw.get("risks", []))
        next_link = raw.get("next")
        path = _normalise_next_link(client, next_link)
    return risks


def _fetch_deep_risk(client, risk_id):
    raw = client.get(f"/v2/entity-screening/risks/{risk_id}")
    try:
        return _parse_risk_detail(raw)
    except Exception as exc:
        raise CAUnexpectedResponse("ComplyAdvantage deep-risk response malformed") from exc


def _fetch_alerts_and_deep_risks(client, workflow_raw, alert_ids=None):
    alerts = []
    deep_risks = {}
    for alert_id in (alert_ids or _extract_alert_ids(workflow_raw)):
        for risk in _fetch_risks_paginated_for_alert(client, alert_id):
            risk_id = _extract_risk_id(risk)
            alerts.append(CAAlertResponse.model_validate(_normalise_risk_as_alert(risk_id, risk, alert_id=alert_id)))
            deep_risks[risk_id] = _fetch_deep_risk(client, risk_id)
    return alerts, deep_risks


def _normalise_next_link(client, next_link):
    if not next_link:
        return None
    parsed = urlparse(next_link)
    if not parsed.scheme and not parsed.netloc:
        return next_link
    base = getattr(getattr(client, "config", None), "api_base_url", "")
    base_parsed = urlparse(base)
    if (parsed.scheme, parsed.netloc) != (base_parsed.scheme, base_parsed.netloc):
        raise CAUnexpectedResponse("ComplyAdvantage pagination next host unexpected")
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def _workflow_errored(workflow):
    """True when CA reports the workflow (or any step) as a terminal ERRORED state.

    ERRORED includes any status CA returns that we don't otherwise model — the
    ScreeningStatus enum maps unknown values to ERRORED — so a provider error or
    a novel status is handled as a terminal failure rather than crashing or
    polling forever.
    """
    if _status_value(workflow.status) == "ERRORED":
        return True
    for detail in (workflow.step_details or {}).values():
        if detail is not None and _status_value(detail.status) == "ERRORED":
            return True
    return False


CUSTOMER_CONFLICT_DEGRADED_SOURCE = "complyadvantage_customer_identifier_conflict"
CUSTOMER_CONFLICT_FLAG = (
    "Re-screen blocked: ComplyAdvantage already holds a customer for this subject's external "
    "identifier and rejected duplicate creation. This is not evidence of zero hits; the "
    "existing-customer re-screen path (RESCREEN-1) is required before this screening can complete."
)


def _customer_identifier_conflict(raw):
    """Detect the Mesh customer-creation identifier conflict from raw workflow JSON.

    SRP-2 batch 1: re-screening an already-screened subject errors the
    customer-creation step because the deterministic external identifier is
    already assigned to an existing Mesh customer. The step's error payload
    shape is not modelled, so detection is tolerant text matching over the
    raw customer-creation step detail.
    """
    if not isinstance(raw, dict):
        return False
    detail = (raw.get("step_details") or {}).get("customer-creation")
    if not isinstance(detail, dict):
        return False
    try:
        text = json.dumps(detail, default=str).lower()
    except Exception:
        return False
    if "external identifier" in text or "external_identifier" in text:
        return "already" in text or "duplicate" in text or "exists" in text or "assigned" in text
    return "already assigned" in text or "already exists" in text


def _mark_report_customer_conflict(report):
    """Stamp the distinct, honest conflict classification onto the report."""
    sources = report.setdefault("degraded_sources", [])
    if CUSTOMER_CONFLICT_DEGRADED_SOURCE not in sources:
        sources.append(CUSTOMER_CONFLICT_DEGRADED_SOURCE)
    flags = report.setdefault("overall_flags", [])
    if CUSTOMER_CONFLICT_FLAG not in flags:
        flags.append(CUSTOMER_CONFLICT_FLAG)
    report["customer_identifier_conflict"] = True


def _workflow_complete(workflow):
    status = _status_value(workflow.status)
    case_detail = workflow.step_details.get("case-creation")
    case_status = _status_value(case_detail.status) if case_detail else None
    if status == "COMPLETED" and case_status in (None, "COMPLETED", "SKIPPED"):
        return True
    if status in ("NOT-STARTED", "IN-PROGRESS") or case_status in ("NOT-STARTED", "IN-PROGRESS"):
        return False
    raise CAUnexpectedResponse("ComplyAdvantage workflow status unexpected")


def _normalise_risk_as_alert(risk_id, raw, *, alert_id=None):
    data = {"identifier": risk_id}
    if alert_id:
        data["alert_identifier"] = alert_id
    profile = _normalise_alert_risk_profile(risk_id, raw)
    if profile is not None:
        data["profile"] = profile
    data.setdefault("risk_details", {"values": []})
    return data


def _normalise_alert_risk_profile(risk_id, raw):
    profile = _mesh_profile_from_risk(raw)
    if not isinstance(profile, dict):
        return None
    try:
        if "match_details" in profile:
            return CAProfile.model_validate(profile).model_dump(mode="json")
        adapted = _adapt_mesh_alert_profile(risk_id, profile)
        if adapted is None:
            return None
        return CAProfile.model_validate(adapted).model_dump(mode="json")
    except Exception:
        logger.warning(
            "ca_alert_risk_profile_parse_failed risk_id=%s profile_shape=ignored",
            risk_id,
            exc_info=True,
        )
        return None


def _adapt_mesh_alert_profile(risk_id, profile):
    if not isinstance(profile, dict):
        return None
    identifier = _text_or_none(profile.get("identifier")) or str(risk_id)
    matching_name = _first_text(
        profile.get("matching_name"),
        profile.get("display_name"),
        profile.get("name"),
        profile.get("caption"),
    )
    company = _adapt_profile_subject(profile.get("company"), matching_name, company=True)
    person = _adapt_profile_subject(profile.get("person"), matching_name, company=False)
    vessel = profile.get("vessel") if isinstance(profile.get("vessel"), dict) else None
    if company is None and person is None and vessel is None:
        if matching_name:
            company = {"names": {"values": [{"name": matching_name}]}}
        else:
            company = {}
    raw_score = profile.get("match_score")
    # CA sandbox returned 0.7 and 1.7 for exact_match risks, so match_score is
    # captured as provider raw score and not rendered as a percentage pending CA
    # scale clarification.
    provider_match_score_raw = raw_score if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool) else None
    indicators = profile.get("risk_indicators") if isinstance(profile.get("risk_indicators"), dict) else {}
    data = {
        "identifier": identifier,
        "entity_type": profile.get("entity_type"),
        "matching_name": matching_name,
        "match_details": {"matched_name": matching_name} if matching_name else {},
        "risk_types": [],
        "risk_indicators": [],
        "provider_match_score_raw": provider_match_score_raw,
        "provider_match_types": _string_list(profile.get("match_types")),
        "provider_aml_types_raw": _string_list(indicators.get("aml_types")),
        "provider_media_evidence": _media_evidence_from_indicators(indicators),
    }
    if company is not None:
        data["company"] = company
    if person is not None:
        data["person"] = person
    if vessel is not None:
        data["vessel"] = vessel
    return {key: value for key, value in data.items() if value not in (None, [])}


def _adapt_profile_subject(value, fallback_name=None, *, company):
    if not isinstance(value, dict):
        return None
    subject = dict(value)
    subject["names"] = _adapt_profile_names(subject.get("names"), fallback_name, company=company)
    if not company:
        # SRP-3 Phase A: normalise the matched person's date of birth into the
        # CADateOfBirth shape. Raw Mesh payloads carry DOB in several forms
        # (ISO string, {year,...} dict, bare year, dates_of_birth list); an
        # unnormalised string previously failed CAProfile validation and
        # silently dropped the WHOLE profile. Unparseable values are omitted,
        # never guessed — the triage score treats absent DOB as "no
        # corroboration", not as a conflict.
        dob = _normalise_profile_date_of_birth(
            subject.get("date_of_birth"),
            subject.get("dates_of_birth"),
            subject.get("birth_date"),
            subject.get("dob"),
            subject.get("year_of_birth"),
        )
        subject.pop("dates_of_birth", None)
        subject.pop("birth_date", None)
        subject.pop("dob", None)
        subject.pop("year_of_birth", None)
        if dob is not None:
            subject["date_of_birth"] = dob
        else:
            subject.pop("date_of_birth", None)
        countries = subject.get("countries")
        if isinstance(countries, str) and countries.strip():
            subject["countries"] = [countries.strip()]
    return subject


def _normalise_profile_date_of_birth(*candidates):
    """Best-effort DOB normalisation to {year, month, day, date}. None on failure."""
    for candidate in candidates:
        if isinstance(candidate, list):
            for entry in candidate:
                normalised = _normalise_profile_date_of_birth(entry)
                if normalised is not None:
                    return normalised
            continue
        if isinstance(candidate, dict):
            year = candidate.get("year")
            try:
                year = int(year) if year is not None else None
            except (TypeError, ValueError):
                year = None
            if year and 1000 <= year <= 9999:
                # Structured fields: the year is explicit provider data. Month/
                # day parts are kept ONLY when they are valid calendar values;
                # out-of-range parts are dropped, never passed through.
                out = {"year": year}
                month = _int_in_range(candidate.get("month"), 1, 12)
                day = _int_in_range(candidate.get("day"), 1, 31)
                if month:
                    out["month"] = month
                if month and day and _valid_calendar_date(year, month, day):
                    out["day"] = day
                if isinstance(candidate.get("date"), str) and candidate["date"].strip():
                    out["date"] = candidate["date"].strip()
                return out
            continue
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int):
            if 1000 <= candidate <= 9999:
                return {"year": candidate}
            continue
        if isinstance(candidate, str):
            # Strings must FULLY match a supported form — a malformed value
            # like "1961-xx" or "1961-99-99" is rejected outright, never
            # partially salvaged into a guessed year (Codex review finding,
            # PR #790 merge gate).
            text = candidate.strip()
            if not text:
                continue
            match = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", text)
            if not match:
                continue
            year = int(match.group(1))
            if not 1000 <= year <= 9999:
                continue
            out = {"year": year}
            if match.group(2):
                month = _int_in_range(match.group(2), 1, 12)
                if month is None:
                    continue  # e.g. 1961-99: whole value is malformed
                out["month"] = month
                if match.group(3):
                    day = _int_in_range(match.group(3), 1, 31)
                    if day is None or not _valid_calendar_date(year, month, day):
                        continue  # e.g. 1961-02-30
                    out["day"] = day
                    out["date"] = f"{year}-{month:02d}-{day:02d}"
            return out
    return None


def _int_in_range(value, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def _valid_calendar_date(year, month, day):
    from datetime import date

    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _adapt_profile_names(value, fallback_name=None, *, company):
    values = []
    if isinstance(value, dict):
        raw_values = value.get("values") if isinstance(value.get("values"), list) else []
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = []
    for item in raw_values:
        if isinstance(item, dict):
            name = _first_text(item.get("name"), item.get("value"), item.get("full_name"))
            if name:
                current = {"name": name}
                if item.get("type"):
                    current["type"] = item.get("type")
                values.append(current)
        else:
            name = _text_or_none(item)
            if name:
                values.append({"name": name})
    if not values and fallback_name:
        values.append({"name": fallback_name})
    return {"values": values}


def _first_text(*values):
    for value in values:
        text = _text_or_none(value)
        if text:
            return text
    return None


def _text_or_none(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_alert_ids(workflow_raw):
    alerts = workflow_raw.get("alerts") or []
    ids = [_extract_identifier(item) for item in alerts]
    step_details = workflow_raw.get("step_details") or {}
    alerting = step_details.get("alerting") or {}
    step_output = alerting.get("step_output") or alerting.get("output") or {}
    step_alerts = step_output.get("alerts") or []
    ids.extend(_extract_identifier(item) for item in step_alerts)
    return list(dict.fromkeys(value for value in ids if value))


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


def _extract_customer_identifier(workflow_raw, *, required=True):
    step_details = workflow_raw.get("step_details") or {}
    customer_creation = step_details.get("customer-creation") or {}
    output = customer_creation.get("step_output") or customer_creation.get("output") or {}
    customer_identifier = output.get("customer_identifier") or output.get("identifier")
    if not customer_identifier:
        customer_identifier = workflow_raw.get("customer_identifier")
    if not customer_identifier:
        if not required:
            # Degraded passes (timed-out / errored) never completed customer
            # creation, so there is no identifier — that is not an error.
            return ""
        raise CAUnexpectedResponse("ComplyAdvantage workflow customer identifier missing")
    return customer_identifier


def _parse_risk_detail(raw):
    mesh_profile = _mesh_profile_from_risk(raw)
    if mesh_profile is not None:
        return _parse_mesh_profile_risk_detail(mesh_profile)

    values = []
    for item in raw.get("values", []):
        risk_type = CARiskType.model_validate(item["risk_type"])
        indicators = []
        for indicator in item.get("indicators", []):
            indicators.append(_parse_indicator(risk_type, indicator.get("value", {})))
        values.append(CARiskDetailInner(risk_type=risk_type, indicators=indicators))
    return CARiskDetail(values=values)


def _mesh_profile_from_risk(raw):
    detail = raw.get("detail") if isinstance(raw, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("profile"), dict):
        return detail["profile"]
    profile = raw.get("profile") if isinstance(raw, dict) else None
    return profile if isinstance(profile, dict) else None


def _parse_mesh_profile_risk_detail(profile):
    indicators = profile.get("risk_indicators") or {}
    if isinstance(indicators, list):
        return CARiskDetail(values=_parse_mesh_list_risk_indicators(indicators))
    if not isinstance(indicators, dict):
        return CARiskDetail(values=[])

    values = []
    values.extend(_parse_mesh_pep_indicators(indicators))
    values.extend(_parse_mesh_media_indicators(indicators))
    values.extend(_parse_mesh_watchlist_indicators(indicators))
    values.extend(_parse_mesh_aml_type_fallback_indicators(
        indicators,
        existing_categories={_category_from_risk_type_key(item.risk_type.key) for item in values},
    ))
    return CARiskDetail(values=values)


def _parse_mesh_list_risk_indicators(indicators):
    parsed = []
    for group in indicators:
        if not isinstance(group, dict):
            continue
        risk_types = group.get("risk_types") if isinstance(group.get("risk_types"), list) else []
        for risk_type_raw in risk_types:
            if not isinstance(risk_type_raw, dict):
                continue
            risk_type = _mesh_risk_type(risk_type_raw)
            key = risk_type.key
            if key in _PEP_RISK_KEY_TO_CLASS:
                parsed.extend(_parse_mesh_list_pep_group(group, risk_type))
            elif key.startswith(_ADVERSE_MEDIA_RISK_PREFIX):
                parsed.extend(_parse_mesh_list_media_group(group, risk_type))
            elif key.startswith(_SANCTIONS_EXPOSURE_PREFIX) or key in _WATCHLIST_RISK_KEYS:
                parsed.extend(_parse_mesh_list_watchlist_group(group, risk_type))
    return parsed


def _mesh_risk_type(raw):
    return CARiskType(
        key=raw.get("key") or raw.get("taxonomy") or "",
        label=raw.get("name") or raw.get("label") or raw.get("key") or "",
    )


def _parse_mesh_list_pep_group(group, risk_type):
    values = _indicator_values(group, "pep_indicators")
    if not values:
        values = [{}]
    label, class_value = _PEP_RISK_KEY_TO_CLASS[risk_type.key]
    if not risk_type.label:
        risk_type = CARiskType(key=risk_type.key, label=label)
    parsed = []
    for item in values:
        if not isinstance(item, dict):
            continue
        value = CAPEPValue.model_validate({
            "class": item.get("class") or class_value,
            "position": _first_list_value(item.get("political_positions")),
            "country": _first_list_value(item.get("issuing_jurisdictions")) or _mesh_location_country(item),
            "level": item.get("level"),
            "scope_of_influence": item.get("scope_of_influence"),
            "political_position_type": item.get("political_position_type"),
            "institution_type": item.get("institution_type"),
            "active_start_date": _mesh_date(item.get("active_start_date")),
            "active_end_date": _mesh_date(item.get("active_end_date")),
            "issuing_jurisdictions": _string_list(item.get("issuing_jurisdictions")),
            "source_metadata": {
                "source": "mesh_profile_risk_indicators",
                "source_identifier": item.get("source_identifier"),
                "source_name": item.get("source_name"),
                "url": item.get("url"),
            },
        })
        parsed.append(CARiskDetailInner(risk_type=risk_type, indicators=[
            CAPEPIndicator(risk_type=risk_type, value=value)
        ]))
    return parsed


def _parse_mesh_list_media_group(group, risk_type):
    values = (
        _indicator_values(group, "media_indicators")
        or _indicator_values(group, "adverse_media_indicators")
    )
    parsed = []
    for item in values:
        if not isinstance(item, dict):
            continue
        value = CAMediaArticleValue.model_validate({
            "title": item.get("title") or item.get("headline") or item.get("name"),
            "url": item.get("url") or item.get("link"),
            "publication_date": _mesh_date(
                item.get("publication_date") or item.get("publishing_date") or item.get("published_at") or item.get("date")
            ),
            "snippets": _mesh_snippets(item),
            "source_name": item.get("source_name") or item.get("source"),
            "categories": _string_list(item.get("aml_types")),
            "source_metadata": {
                "source": "mesh_profile_risk_indicators",
                "identifier": item.get("identifier"),
            },
        })
        parsed.append(CARiskDetailInner(risk_type=risk_type, indicators=[
            CAMediaIndicator(risk_type=risk_type, value=value)
        ]))
    return parsed


def _parse_mesh_list_watchlist_group(group, risk_type):
    values = (
        _indicator_values(group, "sanction_indicators")
        or _indicator_values(group, "sanctions_indicators")
        or _indicator_values(group, "watchlist_indicators")
    )
    parsed = []
    for item in values:
        if not isinstance(item, dict):
            continue
        value = CAWatchlistValue.model_validate({
            "list_name": item.get("source_name") or item.get("name") or item.get("list_name"),
            "authority": item.get("authority"),
            "issuing_jurisdictions": _string_list(item.get("issuing_jurisdictions")),
            "start_date": _mesh_date(item.get("start_date")),
            "end_date": _mesh_date(item.get("end_date")),
            "source_metadata": {"source": "mesh_profile_risk_indicators"},
        })
        parsed.append(CARiskDetailInner(risk_type=risk_type, indicators=[
            CAWatchlistIndicator(risk_type=risk_type, value=value)
        ]))
    return parsed


def _indicator_values(group, key):
    block = group.get(key)
    if isinstance(block, dict) and isinstance(block.get("values"), list):
        return block["values"]
    if isinstance(block, list):
        return block
    return []


def _mesh_date(value):
    if isinstance(value, dict):
        return value.get("date") or value.get("value")
    return value


def _string_list(values):
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        text = _text_or_none(value)
        if text:
            result.append(text)
    return result


def _media_evidence_from_indicators(indicators):
    media = indicators.get("media") if isinstance(indicators, dict) else None
    if not isinstance(media, list):
        return []
    evidence = []
    for item in media:
        if not isinstance(item, dict):
            continue
        evidence.append({
            key: item.get(key)
            for key in ("url", "title", "snippet", "publishing_date", "identifier")
            if item.get(key) not in (None, "", [], {})
        })
    return evidence


def _mesh_snippets(item):
    snippet = _first_text(item.get("snippet"), item.get("summary"))
    return [{"text": snippet}] if snippet else []


def _mesh_location_country(item):
    locations = item.get("locations")
    if isinstance(locations, list) and locations:
        first = locations[0]
        if isinstance(first, dict):
            return first.get("country") or first.get("country_code")
    return None


def _parse_mesh_pep_indicators(indicators):
    peps = indicators.get("peps") or []
    aml_types = indicators.get("aml_types") or []
    if not isinstance(peps, list):
        peps = []
    if not isinstance(aml_types, list):
        aml_types = []

    pep_aml_types = [
        value for value in aml_types
        if isinstance(value, str) and value.lower() in _PEP_AML_TYPE_TO_CLASS
    ]
    if not peps and pep_aml_types:
        peps = [{"aml_types": pep_aml_types}]

    parsed = []
    for pep in peps:
        if not isinstance(pep, dict):
            continue
        pep_types = pep.get("aml_types") if isinstance(pep.get("aml_types"), list) else pep_aml_types
        pep_type = next(
            (value.lower() for value in pep_types if isinstance(value, str) and value.lower() in _PEP_AML_TYPE_TO_CLASS),
            pep_aml_types[0].lower() if pep_aml_types else "pep-class-1",
        )
        key, label, class_value = _PEP_AML_TYPE_TO_CLASS[pep_type]
        risk_type = CARiskType(key=key, label=label)
        value = CAPEPValue.model_validate({
            "class": class_value,
            "position": _mesh_field_value(pep, "political_position"),
            "country": _first_list_value(pep.get("country_codes")) or _mesh_field_value(pep, "political_region"),
            "active_start_date": _first_list_value(pep.get("active_start_dates")),
            "active_end_date": _first_list_value(pep.get("active_end_dates")),
            "source_metadata": {"source": "mesh_profile_risk_indicators"},
        })
        parsed.append(CARiskDetailInner(risk_type=risk_type, indicators=[
            CAPEPIndicator(risk_type=risk_type, value=value)
        ]))
    return parsed


def _parse_mesh_media_indicators(indicators):
    media_items = indicators.get("media") or []
    if not isinstance(media_items, list):
        return []
    parsed = []
    risk_type = CARiskType(key="r_adverse_media_general", label="Adverse media")
    for item in media_items:
        if not isinstance(item, dict):
            continue
        value = CAMediaArticleValue.model_validate({
            "title": item.get("title") or item.get("headline") or item.get("name"),
            "url": item.get("url") or item.get("link"),
            "publication_date": item.get("publication_date") or item.get("publishing_date") or item.get("published_at") or item.get("date"),
            "snippets": _mesh_snippets(item),
            "source_name": item.get("source_name") or item.get("source"),
            "categories": item.get("aml_types") if isinstance(item.get("aml_types"), list) else [],
            "source_metadata": {
                "source": "mesh_profile_risk_indicators",
                "identifier": item.get("identifier"),
            },
        })
        parsed.append(CARiskDetailInner(risk_type=risk_type, indicators=[
            CAMediaIndicator(risk_type=risk_type, value=value)
        ]))
    return parsed


def _parse_mesh_watchlist_indicators(indicators):
    lists = indicators.get("lists") or []
    if not isinstance(lists, list):
        return []
    parsed = []
    for item in lists:
        if not isinstance(item, dict):
            continue
        aml_types = item.get("aml_types") if isinstance(item.get("aml_types"), list) else []
        is_sanctions = any("sanction" in str(value).lower() for value in aml_types)
        key = "r_direct_sanctions_exposure" if is_sanctions else "r_watchlist"
        label = "Sanctions exposure" if is_sanctions else "Watchlist"
        risk_type = CARiskType(key=key, label=label)
        value = CAWatchlistValue.model_validate({
            "list_name": item.get("name") or item.get("list_name") or item.get("source"),
            "authority": item.get("authority"),
            "source_metadata": {"source": "mesh_profile_risk_indicators"},
        })
        parsed.append(CARiskDetailInner(risk_type=risk_type, indicators=[
            CAWatchlistIndicator(risk_type=risk_type, value=value)
        ]))
    return parsed


def _parse_mesh_aml_type_fallback_indicators(indicators, *, existing_categories):
    aml_types = _string_list(indicators.get("aml_types")) if isinstance(indicators, dict) else []
    parsed = []
    for category in _categories_from_aml_types(aml_types):
        if category in existing_categories:
            continue
        if category == "pep":
            risk_type = CARiskType(key="r_pep_class_1", label="PEP")
            value = CAPEPValue.model_validate({
                "class": "PEP_CLASS_1",
                "source_metadata": {"source": "mesh_profile_aml_types", "aml_types": aml_types},
            })
            parsed.append(CARiskDetailInner(risk_type=risk_type, indicators=[
                CAPEPIndicator(risk_type=risk_type, value=value)
            ]))
        elif category == "adverse_media":
            risk_type = CARiskType(key="r_adverse_media_general", label="Adverse media")
            value = CAMediaArticleValue.model_validate({
                "source_metadata": {"source": "mesh_profile_aml_types", "aml_types": aml_types},
            })
            parsed.append(CARiskDetailInner(risk_type=risk_type, indicators=[
                CAMediaIndicator(risk_type=risk_type, value=value)
            ]))
        elif category == "sanctions":
            risk_type = CARiskType(key="r_direct_sanctions_exposure", label="Sanctions exposure")
            value = CASanctionValue.model_validate({
                "source_metadata": {"source": "mesh_profile_aml_types", "aml_types": aml_types},
            })
            parsed.append(CARiskDetailInner(risk_type=risk_type, indicators=[
                CASanctionIndicator(risk_type=risk_type, value=value)
            ]))
        elif category == "watchlist":
            risk_type = CARiskType(key="r_watchlist", label="Watchlist")
            value = CAWatchlistValue.model_validate({
                "source_metadata": {"source": "mesh_profile_aml_types", "aml_types": aml_types},
            })
            parsed.append(CARiskDetailInner(risk_type=risk_type, indicators=[
                CAWatchlistIndicator(risk_type=risk_type, value=value)
            ]))
    return parsed


def _categories_from_aml_types(aml_types):
    categories = []
    for value in aml_types or []:
        category = _category_from_aml_type(value)
        if category != "other" and category not in categories:
            categories.append(category)
    return categories


def _category_from_aml_type(value):
    text = _text_or_none(value)
    if not text:
        return "other"
    normalized = text.lower().replace("_", "-").replace(" ", "-")
    if normalized.startswith("sanction"):
        return "sanctions"
    if normalized.startswith("pep") or "politically-exposed" in normalized:
        return "pep"
    if (
        normalized.startswith("adverse-media")
        or normalized.startswith("adverse-media")
        or "negative-news" in normalized
    ):
        return "adverse_media"
    if normalized == "warning" or normalized.startswith("fitness-probity") or "watchlist" in normalized:
        return "watchlist"
    return "other"


def _category_from_risk_type_key(key):
    if not key:
        return "other"
    text = str(key).lower()
    if text.startswith(_PEP_RISK_PREFIX) or text == _RCA_RISK_KEY:
        return "pep"
    if text.startswith(_ADVERSE_MEDIA_RISK_PREFIX):
        return "adverse_media"
    if text.startswith(_SANCTIONS_EXPOSURE_PREFIX) or text == "r_direct_sanctions_exposure":
        return "sanctions"
    if text in _WATCHLIST_RISK_KEYS:
        return "watchlist"
    return "other"


def _mesh_field_value(item, tag):
    fields = item.get("fields") if isinstance(item, dict) else None
    if not isinstance(fields, list):
        return None
    for field in fields:
        if not isinstance(field, dict):
            continue
        if field.get("tag") == tag or field.get("name") == tag:
            return field.get("value")
    return None


def _first_list_value(values):
    if isinstance(values, list) and values:
        return values[0]
    return None


def _parse_indicator(risk_type, value):
    key = risk_type.key
    if key.startswith(_PEP_RISK_PREFIX) or key == _RCA_RISK_KEY:
        return CAPEPIndicator(risk_type=risk_type, value=CAPEPValue.model_validate(value))
    if key.startswith(_ADVERSE_MEDIA_RISK_PREFIX):
        return CAMediaIndicator(risk_type=risk_type, value=CAMediaArticleValue.model_validate(value))
    if key.startswith(_SANCTIONS_EXPOSURE_PREFIX) or key in _WATCHLIST_RISK_KEYS:
        return CAWatchlistIndicator(risk_type=risk_type, value=CAWatchlistValue.model_validate(value))
    return CASanctionIndicator(risk_type=risk_type, value=CASanctionValue.model_validate(value))
