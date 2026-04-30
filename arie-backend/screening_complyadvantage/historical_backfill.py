"""Historical ComplyAdvantage media/PEP/sanctions backfill engine."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from screening_config import get_active_provider_name
from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_storage import persist_normalized_report

from .models import CAAlertResponse, CACustomerInput, CACustomerResponse, CAWorkflowResponse
from .normalizer import ResnapshotContext, ScreeningApplicationContext, normalize_single_pass
from .observability import emit_audit, emit_metric, emit_operational, endpoint_category, status_family
from .orchestrator import _extract_risk_id, _normalise_next_link, _normalise_risk_as_alert
from .webhook_fetch import _parse_deep_risk_preserving_payloads
from .webhook_mapping import map_normalized_to_monitoring_alert

logger = logging.getLogger(__name__)

_MAX_API_CALLS_PER_BACKFILL = 1000
_MAX_PAGES_PER_RESOURCE = 50
_MAX_CONCURRENT_BACKFILLS = 3

_BACKFILL_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_BACKFILLS)


async def run_historical_backfill_for_subscription(
    *,
    db,
    ca_client,
    application_id: str,
    client_id: str,
    customer_identifier: str,
    discovered_via: str = "webhook_backfill",
    trigger_reason: str = "subscription_seed",
    trace_id: str | None = None,
    backfill_run_id: str | None = None,
    agent_executor=None,
) -> dict:
    """Run one bounded historical CA backfill for a seeded subscription."""
    if discovered_via not in {"webhook_backfill", "manual_backfill"}:
        raise ValueError("historical backfill discovered_via must be webhook_backfill or manual_backfill")
    backfill_run_id = backfill_run_id or f"bf-{uuid4().hex}"
    trace_id = trace_id or backfill_run_id
    async with _BACKFILL_SEMAPHORE:
        return await _run_backfill(
            db=db,
            ca_client=ca_client,
            application_id=application_id,
            client_id=client_id,
            customer_identifier=customer_identifier,
            discovered_via=discovered_via,
            trigger_reason=trigger_reason,
            trace_id=trace_id,
            backfill_run_id=backfill_run_id,
            agent_executor=agent_executor,
        )


async def rerun_historical_backfill_for_customer(
    *,
    db,
    ca_client,
    application_id: str,
    client_id: str,
    customer_identifier: str,
    trace_id: str | None = None,
    backfill_run_id: str | None = None,
    agent_executor=None,
) -> dict:
    """Backend-callable manual rerun path. No UI, route, or batch sweep."""
    return await run_historical_backfill_for_subscription(
        db=db,
        ca_client=ca_client,
        application_id=application_id,
        client_id=client_id,
        customer_identifier=customer_identifier,
        discovered_via="manual_backfill",
        trigger_reason="manual_rerun",
        trace_id=trace_id,
        backfill_run_id=backfill_run_id,
        agent_executor=agent_executor,
    )


async def _run_backfill(
    *,
    db,
    ca_client,
    application_id,
    client_id,
    customer_identifier,
    discovered_via,
    trigger_reason,
    trace_id,
    backfill_run_id,
    agent_executor,
):
    started = time.monotonic()
    guard = _BackfillGuard(
        ca_client,
        max_calls=_MAX_API_CALLS_PER_BACKFILL,
        trace_id=trace_id,
        backfill_run_id=backfill_run_id,
    )
    result = {
        "status": "started",
        "application_id": application_id,
        "client_id": client_id,
        "customer_identifier": customer_identifier,
        "trigger_reason": trigger_reason,
        "discovered_via": discovered_via,
        "backfill_run_id": backfill_run_id,
        "api_calls": 0,
        "cases_seen": 0,
        "cases_matched": 0,
        "normalized_records_written": 0,
        "rows_inserted": 0,
        "rows_updated": 0,
        "truncated": False,
        "truncation": None,
    }
    emit_metric(
        "backfill_customers_started",
        metric_name="BackfillCustomersStarted",
        trace_id=trace_id,
        component="historical_backfill",
        outcome="started",
        trigger_reason=trigger_reason,
        discovered_via=discovered_via,
    )
    emit_operational(
        "ca_historical_backfill_started",
        trace_id=trace_id,
        component="historical_backfill",
        outcome="started",
        trigger_reason=trigger_reason,
        backfill_run_id=backfill_run_id,
    )
    try:
        context = ScreeningApplicationContext(
            application_id=application_id,
            client_id=client_id,
            screening_subject_kind="entity",
            screening_subject_name=customer_identifier,
        )
        for case_summary in _fetch_cases_for_customer(guard, customer_identifier):
            result["cases_seen"] += 1
            if guard.truncation:
                break
            if not _case_matches_customer(case_summary, customer_identifier):
                continue
            case_identifier = _case_identifier(case_summary)
            if not case_identifier:
                continue
            result["cases_matched"] += 1
            case_raw = guard.get(f"/v2/cases/{case_identifier}", resource="case", identifier=case_identifier)
            if guard.truncation or case_raw is None:
                break
            alert_ids = [
                alert_id for alert_id in (_alert_identifier(a) for a in _fetch_alerts_for_case(guard, case_identifier))
                if alert_id
            ]
            if guard.truncation:
                break
            normalized_report = _normalize_case_backfill(
                guard,
                application_context=context,
                case_identifier=case_identifier,
                case_raw=case_raw,
                alert_ids=alert_ids,
                customer_identifier=customer_identifier,
                trace_id=trace_id,
            )
            normalized_report.setdefault("application_id", application_id)
            source_hash = normalized_report.get("source_screening_report_hash")
            normalized_record_id = persist_normalized_report(
                db,
                client_id,
                application_id,
                normalized_report,
                source_hash,
                provider=COMPLYADVANTAGE_PROVIDER_NAME,
                normalized_version="2.0",
            )
            _commit(db)
            result["normalized_records_written"] += 1
            emit_audit(
                "ca_backfill_provider_truth_persisted",
                trace_id=trace_id,
                component="historical_backfill",
                outcome="success",
                application_id=application_id,
                client_id=client_id,
                case_identifier=case_identifier,
                backfill_run_id=backfill_run_id,
                normalized_record_id=normalized_record_id,
                authoritative=False,
            )
            if normalized_report.get("total_hits", 0) <= 0:
                continue
            row = map_normalized_to_monitoring_alert(
                normalized_report,
                case_identifier=case_identifier,
                customer_identifier=customer_identifier,
                normalized_record_id=normalized_record_id,
            )
            row["application_id"] = application_id
            outcome = _upsert_monitoring_alert_with_provenance(
                db,
                row,
                discovered_via=discovered_via,
                backfill_run_id=backfill_run_id,
            )
            _commit(db)
            if outcome == "inserted":
                result["rows_inserted"] += 1
            else:
                result["rows_updated"] += 1
        result["api_calls"] = guard.calls
        result["truncated"] = bool(guard.truncation)
        result["truncation"] = guard.truncation
        if guard.truncation:
            emit_metric(
                "backfill_truncated",
                metric_name="BackfillTruncated",
                trace_id=trace_id,
                component="historical_backfill",
                outcome="truncated",
                trigger_reason=trigger_reason,
                truncation_reason=guard.truncation.get("reason"),
            )
            emit_operational(
                "ca_historical_backfill_truncated",
                trace_id=trace_id,
                component="historical_backfill",
                outcome="truncated",
                backfill_run_id=backfill_run_id,
                truncation_reason=guard.truncation.get("reason"),
            )
        emit_metric(
            "backfill_cases_matched_total",
            metric_name="BackfillCasesMatchedTotal",
            value=result["cases_matched"],
            trace_id=trace_id,
            component="historical_backfill",
            outcome="success",
            trigger_reason=trigger_reason,
        )
        emit_metric(
            "backfill_rows_inserted",
            metric_name="BackfillRowsInserted",
            value=result["rows_inserted"],
            trace_id=trace_id,
            component="historical_backfill",
            outcome="success",
            trigger_reason=trigger_reason,
        )
        emit_metric(
            "backfill_rows_updated",
            metric_name="BackfillRowsUpdated",
            value=result["rows_updated"],
            trace_id=trace_id,
            component="historical_backfill",
            outcome="success",
            trigger_reason=trigger_reason,
        )
        _maybe_push_agent7(
            application_id=application_id,
            client_id=client_id,
            customer_identifier=customer_identifier,
            trace_id=trace_id,
            backfill_run_id=backfill_run_id,
            agent_executor=agent_executor,
            result=result,
        )
        result["status"] = "completed"
        emit_metric(
            "backfill_customers_completed",
            metric_name="BackfillCustomersCompleted",
            trace_id=trace_id,
            component="historical_backfill",
            outcome="success",
            trigger_reason=trigger_reason,
        )
        emit_operational(
            "ca_historical_backfill_completed",
            trace_id=trace_id,
            component="historical_backfill",
            outcome="success",
            backfill_run_id=backfill_run_id,
            duration_ms=_elapsed_ms(started),
            api_calls=guard.calls,
            cases_matched=result["cases_matched"],
            rows_inserted=result["rows_inserted"],
            rows_updated=result["rows_updated"],
            truncated=result["truncated"],
        )
        return result
    except Exception as exc:
        result["api_calls"] = guard.calls
        result["status"] = "failed"
        result["error_class"] = exc.__class__.__name__
        _rollback(db)
        emit_metric(
            "backfill_customers_failed",
            metric_name="BackfillCustomersFailed",
            trace_id=trace_id,
            component="historical_backfill",
            outcome="failure",
            trigger_reason=trigger_reason,
            status_family=status_family(error=exc),
        )
        emit_operational(
            "ca_historical_backfill_failed",
            trace_id=trace_id,
            component="historical_backfill",
            outcome="failure",
            backfill_run_id=backfill_run_id,
            exception_class=exc.__class__.__name__,
            duration_ms=_elapsed_ms(started),
        )
        raise


def _fetch_cases_for_customer(guard, customer_identifier):
    path = "/v2/cases"
    params = {"customer_identifier": customer_identifier}
    yield from _fetch_paginated_items(
        guard,
        initial_path=path,
        key="cases",
        resource="cases",
        identifier="customer",
        params=params,
    )


def _fetch_alerts_for_case(guard, case_identifier):
    yield from _fetch_paginated_items(
        guard,
        initial_path=f"/v2/cases/{case_identifier}/alerts",
        key="alerts",
        resource="case_alerts",
        identifier=case_identifier,
    )


def _fetch_risks_for_alert(guard, alert_id):
    yield from _fetch_paginated_items(
        guard,
        initial_path=f"/v2/alerts/{alert_id}/risks",
        key="risks",
        resource="alert_risks",
        identifier=alert_id,
    )


def _fetch_paginated_items(guard, *, initial_path, key, resource, identifier, params=None):
    path = initial_path
    page_count = 0
    while path:
        if page_count >= _MAX_PAGES_PER_RESOURCE:
            guard.mark_truncated("page_cap", resource=resource, identifier=identifier, page_cap=_MAX_PAGES_PER_RESOURCE)
            break
        page_count += 1
        raw = guard.get(path, resource=resource, identifier=identifier, page=page_count, params=params if page_count == 1 else None)
        if raw is None:
            break
        for item in raw.get(key, []):
            yield item
        path = _normalise_next_link(getattr(guard, "client", guard), raw.get("next"))
        params = None


def _normalize_case_backfill(
    guard,
    *,
    application_context,
    case_identifier,
    case_raw,
    alert_ids,
    customer_identifier,
    trace_id,
):
    alerts = []
    deep_risks = {}
    alert_risk_listings = {}
    for alert_id in alert_ids:
        for listing in _fetch_risks_for_alert(guard, alert_id):
            if guard.truncation:
                break
            risk_id = _extract_risk_id(listing)
            alert_risk_listings[risk_id] = listing
            alerts.append(CAAlertResponse.model_validate(_normalise_risk_as_alert(risk_id, listing)))
            deep_raw = guard.get(f"/v2/entity-screening/risks/{risk_id}", resource="deep_risk", identifier=risk_id)
            if deep_raw is None:
                break
            deep_risks[risk_id] = _parse_deep_risk_preserving_payloads(deep_raw, listing)
        if guard.truncation:
            break
    workflow = CAWorkflowResponse.model_validate(_case_backed_workflow(case_identifier, case_raw, alert_ids))
    customer_input = _customer_input(case_raw, customer_identifier)
    customer_response = CACustomerResponse.model_validate({"identifier": customer_identifier})
    report = normalize_single_pass(
        workflow,
        alerts,
        deep_risks,
        customer_input,
        customer_response,
        application_context,
        ResnapshotContext(
            webhook_type="HISTORICAL_BACKFILL",
            source_case_identifier=case_identifier,
            received_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    provider = report.setdefault("provider_specific", {}).setdefault(COMPLYADVANTAGE_PROVIDER_NAME, {})
    provider["alert_risk_listings"] = alert_risk_listings
    provider["historical_backfill"] = {"trace_id": trace_id, "case_identifier": case_identifier}
    if guard.truncation:
        provider["historical_backfill_truncated"] = guard.truncation
    return report


def _case_backed_workflow(case_identifier, case_raw, alert_ids):
    return {
        "workflow_instance_identifier": f"case-backed:{case_identifier}",
        "workflow_type": "case-backed-historical-backfill",
        "status": "IN-PROGRESS",
        "steps": ["historical-backfill"],
        "step_details": {"historical-backfill": {"step_identifier": "historical-backfill", "status": "IN-PROGRESS"}},
        "alerts": [{"identifier": alert_id} for alert_id in alert_ids],
        "case_identifier": case_identifier,
        "case_state": case_raw.get("case_state"),
        "case_stage": case_raw.get("case_stage"),
        "case_detail": case_raw,
    }


def _upsert_monitoring_alert_with_provenance(db, row, *, discovered_via, backfill_run_id):
    existing = db.execute(
        "SELECT discovered_via FROM monitoring_alerts WHERE provider=? AND case_identifier=?",
        (row["provider"], row["case_identifier"]),
    ).fetchone()
    db.execute(
        """
        INSERT INTO monitoring_alerts
            (provider, case_identifier, application_id, client_name, alert_type, severity,
             detected_by, summary, source_reference, status, discovered_via, discovered_at, backfill_run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(provider, case_identifier)
        WHERE provider IS NOT NULL AND case_identifier IS NOT NULL
        DO UPDATE SET
            application_id = EXCLUDED.application_id,
            client_name = EXCLUDED.client_name,
            alert_type = EXCLUDED.alert_type,
            severity = EXCLUDED.severity,
            detected_by = EXCLUDED.detected_by,
            summary = EXCLUDED.summary,
            source_reference = EXCLUDED.source_reference,
            status = EXCLUDED.status,
            discovered_via = CASE
                WHEN monitoring_alerts.discovered_via = 'webhook_live' THEN monitoring_alerts.discovered_via
                ELSE EXCLUDED.discovered_via
            END,
            discovered_at = COALESCE(monitoring_alerts.discovered_at, EXCLUDED.discovered_at),
            backfill_run_id = EXCLUDED.backfill_run_id
        """,
        (
            row["provider"],
            row["case_identifier"],
            row["application_id"],
            row["client_name"],
            row["alert_type"],
            row["severity"],
            row["detected_by"],
            row["summary"],
            row["source_reference"],
            row["status"],
            discovered_via,
            backfill_run_id,
        ),
    )
    return "updated" if existing else "inserted"


def _maybe_push_agent7(*, application_id, client_id, customer_identifier, trace_id, backfill_run_id, agent_executor, result):
    active_provider = get_active_provider_name()
    if active_provider != COMPLYADVANTAGE_PROVIDER_NAME:
        result["agent7_push"] = "skipped"
        emit_metric(
            "backfill_agent7_push_skipped",
            metric_name="BackfillAgent7PushSkipped",
            trace_id=trace_id,
            component="historical_backfill",
            outcome="skipped",
            active_provider=active_provider,
            step="agent7_push",
        )
        emit_audit(
            "ca_backfill_agent7_push_skipped_shadow_mode",
            trace_id=trace_id,
            component="historical_backfill",
            outcome="skipped",
            application_id=application_id,
            client_id=client_id,
            customer_identifier=customer_identifier,
            backfill_run_id=backfill_run_id,
            decision_context="shadow_mode",
        )
        return
    try:
        executor = agent_executor or _default_agent_executor()
        executor(application_id, {"db_path": _default_db_path()})
        result["agent7_push"] = "attempted"
    except Exception as exc:
        result["agent7_push"] = "failed"
        emit_metric(
            "backfill_agent7_push_failed",
            metric_name="BackfillAgent7PushFailed",
            trace_id=trace_id,
            component="historical_backfill",
            outcome="failure",
            active_provider=active_provider,
            step="agent7_push",
            status_family=status_family(error=exc),
        )
        logger.error("ca_backfill_agent7_push_failed application_id=%s", application_id, exc_info=True)


class _BackfillGuard:
    def __init__(self, client, max_calls=_MAX_API_CALLS_PER_BACKFILL, trace_id=None, backfill_run_id=None):
        self.client = client
        self.max_calls = max_calls
        self.trace_id = trace_id
        self.backfill_run_id = backfill_run_id
        self.calls = 0
        self.truncation = None

    def get(self, path, *, resource=None, identifier=None, page=None, params=None):
        category = endpoint_category(path)
        if self.calls >= self.max_calls:
            self.mark_truncated("api_call_budget", resource=resource, identifier=identifier, api_call_budget=self.max_calls)
            return None
        self.calls += 1
        try:
            raw = self.client.get(path, params=params)
        except TypeError:
            raw = self.client.get(path)
        emit_metric(
            "backfill_api_calls_total",
            metric_name="BackfillApiCallsTotal",
            trace_id=self.trace_id,
            component="historical_backfill",
            outcome="success",
            endpoint_category=category,
        )
        emit_operational(
            "ca_historical_backfill_api_call",
            trace_id=self.trace_id,
            component="historical_backfill",
            outcome="success",
            endpoint_category=category,
            resource=resource,
            page=page,
            backfill_run_id=self.backfill_run_id,
        )
        return raw

    def mark_truncated(self, reason, **fields):
        if self.truncation is None:
            self.truncation = {"reason": reason, "calls": self.calls, **fields}


def _case_matches_customer(case_raw, customer_identifier):
    candidates = {
        case_raw.get("customer_identifier"),
        (case_raw.get("customer") or {}).get("identifier") if isinstance(case_raw.get("customer"), dict) else None,
        (case_raw.get("customer") or {}).get("external_identifier") if isinstance(case_raw.get("customer"), dict) else None,
    }
    return customer_identifier in {value for value in candidates if value}


def _case_identifier(case_raw):
    return case_raw.get("case_identifier") or case_raw.get("identifier") or case_raw.get("id")


def _alert_identifier(alert_raw):
    if isinstance(alert_raw, str):
        return alert_raw
    if isinstance(alert_raw, dict):
        return alert_raw.get("identifier") or alert_raw.get("id") or alert_raw.get("alert_identifier")
    return None


def _customer_input(case_raw, fallback_name):
    raw_customer = case_raw.get("customer_input") or case_raw.get("customer") or {}
    if isinstance(raw_customer, dict) and (raw_customer.get("person") or raw_customer.get("company")):
        return CACustomerInput.model_validate(raw_customer)
    return CACustomerInput.model_validate({"company": {"name": fallback_name}})


def _commit(db):
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()


def _rollback(db):
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        rollback()


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)


def _default_agent_executor():
    from supervisor.agent_executors import execute_adverse_media_pep
    return execute_adverse_media_pep


def _default_db_path():
    from config import DB_PATH
    return DB_PATH
