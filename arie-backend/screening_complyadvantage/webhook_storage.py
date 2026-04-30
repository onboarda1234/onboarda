"""Storage orchestration for ComplyAdvantage webhook dual-write processing."""

import logging
import time
from datetime import datetime, timezone
from inspect import Parameter, signature

from screening_config import get_active_provider_name
from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_storage import persist_normalized_report

from .normalizer import ScreeningApplicationContext
from .observability import emit_audit, emit_metric as _emit_metric, emit_operational
from .subscriptions import update_monitoring_subscription_event
from .webhook_fetch import build_default_client, fetch_webhook_single_pass
from .webhook_mapping import map_normalized_to_monitoring_alert

logger = logging.getLogger(__name__)


def emit_metric(name, **fields):
    component = fields.pop("component", "webhook_storage")
    return _emit_metric(name, component=component, **fields)


async def process_complyadvantage_webhook(
    envelope,
    *,
    trace_id=None,
    db_factory=None,
    client_factory=None,
    fetch_normalized=fetch_webhook_single_pass,
    persist_report=persist_normalized_report,
    agent_executor=None,
):
    """Run the locked 9-step C4 dual-write sequence after HTTP 202."""
    db_factory = db_factory or _default_db_factory
    client_factory = client_factory or build_default_client
    customer_identifier = getattr(envelope.customer, "identifier", None)
    case_identifier = envelope.case_identifier
    webhook_type = getattr(envelope, "webhook_type", "none")
    processing_started = time.monotonic()
    final_outcome = "failure"

    # Step 1 — Read-only: envelope already parsed by the route handler.
    db = db_factory()
    try:
        subscription = _lookup_subscription(db, customer_identifier)
    finally:
        _close(db)
    if subscription is None:
        logger.warning(
            "ca_webhook_subscription_missing webhook_type=%s case_identifier=%s customer_identifier=%s",
            envelope.webhook_type,
            case_identifier,
            customer_identifier,
        )
        emit_metric(
            "webhook_step_result",
            metric_name="WebhookStepLatencyMs",
            value=0,
            unit="Milliseconds",
            trace_id=trace_id,
            component="webhook_storage",
            outcome="skipped",
            webhook_type=webhook_type,
            step="subscription_lookup",
            case_identifier=case_identifier,
            customer_identifier=customer_identifier,
        )
        emit_metric(
            "webhook_processing_latency",
            metric_name="WebhookProcessingLatencyMs",
            value=_elapsed_ms(processing_started),
            unit="Milliseconds",
            trace_id=trace_id,
            component="webhook_storage",
            outcome="skipped",
            webhook_type=webhook_type,
        )
        return {"status": "subscription_missing"}
    if subscription == "ambiguous":
        logger.error(
            "ca_webhook_subscription_ambiguous webhook_type=%s case_identifier=%s customer_identifier=%s",
            envelope.webhook_type,
            case_identifier,
            customer_identifier,
        )
        emit_metric(
            "webhook_processing_latency",
            metric_name="WebhookProcessingLatencyMs",
            value=_elapsed_ms(processing_started),
            unit="Milliseconds",
            trace_id=trace_id,
            component="webhook_storage",
            outcome="failure",
            webhook_type=webhook_type,
        )
        return {"status": "subscription_ambiguous"}

    # Step 2 — Pure compute: build ScreeningApplicationContext from subscription.
    application_context = _application_context_from_subscription(subscription)

    # Step 3 — Read-only: fetch-back through shared three-layer helpers.
    client = client_factory()
    fetch_started = time.monotonic()
    normalized_report = _call_fetch_normalized(fetch_normalized, client, envelope, application_context, trace_id)
    normalized_report.setdefault("application_id", application_context.application_id)
    emit_metric(
        "webhook_step_result",
        metric_name="WebhookStepLatencyMs",
        value=_elapsed_ms(fetch_started),
        unit="Milliseconds",
        trace_id=trace_id,
        component="webhook_storage",
        outcome="success",
        webhook_type=webhook_type,
        step="fetch_back",
    )

    # Step 4 — Pure compute: normalization completed by webhook_fetch.
    source_hash = normalized_report.get("source_screening_report_hash")
    if not source_hash:
        raise ValueError("normalized CA report missing source_screening_report_hash")

    normalized_record_id = None
    db = db_factory()
    try:
        # Step 5 — REQUIRED idempotent: upsert provider-truth normalized record.
        try:
            step_started = time.monotonic()
            normalized_record_id = persist_report(
                db,
                application_context.client_id,
                application_context.application_id,
                normalized_report,
                source_hash,
                provider=COMPLYADVANTAGE_PROVIDER_NAME,
                normalized_version="2.0",
            )
            _commit(db)
            emit_metric(
                "normalized_write_success",
                metric_name="NormalizedWriteSuccesses",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                step="normalized_write",
            )
            emit_metric(
                "webhook_step_result",
                metric_name="WebhookStepLatencyMs",
                value=_elapsed_ms(step_started),
                unit="Milliseconds",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                webhook_type=webhook_type,
                step="normalized_write",
            )
            emit_audit(
                "ca_provider_truth_persisted",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                application_id=application_context.application_id,
                client_id=application_context.client_id,
                source_screening_report_hash=source_hash,
                normalized_record_id=normalized_record_id,
                webhook_type=webhook_type,
                case_identifier=case_identifier,
                customer_identifier=customer_identifier,
                authoritative=False,
            )
        except Exception:
            _rollback(db)
            logger.error(
                "ca_webhook_normalized_write_failure case_identifier=%s customer_identifier=%s",
                case_identifier,
                customer_identifier,
                exc_info=True,
            )
            emit_metric(
                "normalized_write_failure",
                provider=COMPLYADVANTAGE_PROVIDER_NAME,
                trace_id=trace_id,
                component="webhook_storage",
                outcome="failure",
                step="normalized_write",
                case_identifier=case_identifier,
                customer_identifier=customer_identifier,
            )
            emit_metric(
                "webhook_processing_latency",
                metric_name="WebhookProcessingLatencyMs",
                value=_elapsed_ms(processing_started),
                unit="Milliseconds",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="failure",
                webhook_type=webhook_type,
            )
            return {"status": "normalized_write_failure"}
    finally:
        _close(db)

    # Step 6 — Pure compute: map normalized record to monitoring_alerts row.
    alert_row = map_normalized_to_monitoring_alert(
        normalized_report,
        case_identifier=case_identifier,
        customer_identifier=customer_identifier,
        normalized_record_id=normalized_record_id,
    )
    alert_row["application_id"] = application_context.application_id

    db = db_factory()
    try:
        # Step 7 — BEST-EFFORT (failure logs + metric, sequence continues): upsert monitoring_alerts.
        try:
            step_started = time.monotonic()
            _upsert_monitoring_alert(db, alert_row)
            _commit(db)
            emit_metric(
                "monitoring_alerts_write_success",
                metric_name="MonitoringAlertsWriteSuccesses",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                step="monitoring_alert_write",
            )
            emit_metric(
                "webhook_step_result",
                metric_name="WebhookStepLatencyMs",
                value=_elapsed_ms(step_started),
                unit="Milliseconds",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                webhook_type=webhook_type,
                step="monitoring_alert_write",
            )
            emit_audit(
                "ca_monitoring_alert_upserted",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                application_id=application_context.application_id,
                client_id=application_context.client_id,
                normalized_record_id=normalized_record_id,
                webhook_type=webhook_type,
                case_identifier=case_identifier,
                customer_identifier=customer_identifier,
            )
        except Exception:
            _rollback(db)
            logger.error(
                "ca_webhook_monitoring_alerts_write_failure case_identifier=%s customer_identifier=%s",
                case_identifier,
                customer_identifier,
                exc_info=True,
            )
            emit_metric(
                "monitoring_alerts_write_failure",
                provider=COMPLYADVANTAGE_PROVIDER_NAME,
                trace_id=trace_id,
                component="webhook_storage",
                outcome="failure",
                step="monitoring_alert_write",
                case_identifier=case_identifier,
                customer_identifier=customer_identifier,
            )
    finally:
        _close(db)

    db = db_factory()
    try:
        # Step 8 — BEST-EFFORT (failure logs + metric, sequence continues): update subscription event metadata.
        try:
            step_started = time.monotonic()
            update_monitoring_subscription_event(
                db,
                application_context.client_id,
                customer_identifier,
                envelope.webhook_type,
                trace_id=trace_id,
            )
            emit_metric(
                "webhook_step_result",
                metric_name="WebhookStepLatencyMs",
                value=_elapsed_ms(step_started),
                unit="Milliseconds",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                webhook_type=webhook_type,
                step="subscription_update",
            )
            emit_audit(
                "ca_subscription_event_recorded",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                application_id=application_context.application_id,
                client_id=application_context.client_id,
                webhook_type=webhook_type,
                case_identifier=case_identifier,
                customer_identifier=customer_identifier,
            )
        except Exception:
            _rollback(db)
            logger.warning(
                "ca_webhook_subscription_update_failure case_identifier=%s customer_identifier=%s",
                case_identifier,
                customer_identifier,
                exc_info=True,
            )
            emit_metric(
                "subscription_update_failure",
                provider=COMPLYADVANTAGE_PROVIDER_NAME,
                trace_id=trace_id,
                component="webhook_storage",
                outcome="failure",
                step="subscription_update",
                case_identifier=case_identifier,
                customer_identifier=customer_identifier,
            )
    finally:
        _close(db)

    # Step 9 — BEST-EFFORT (failure logs + metric, sequence continues): flag-aware Agent 7 push.
    active_provider = get_active_provider_name()
    if active_provider == COMPLYADVANTAGE_PROVIDER_NAME:
        emit_audit(
            "ca_agent7_push_attempted",
            trace_id=trace_id,
            component="webhook_storage",
            outcome="success",
            application_id=application_context.application_id,
            client_id=application_context.client_id,
            webhook_type=webhook_type,
            case_identifier=case_identifier,
            customer_identifier=customer_identifier,
            decision_context="active_cutover",
        )
        try:
            step_started = time.monotonic()
            executor = agent_executor or _default_agent_executor()
            context = {"db_path": _default_db_path()}
            executor(application_context.application_id, context)
            emit_metric(
                "agent_7_push_success",
                metric_name="Agent7PushSuccesses",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                active_provider=active_provider,
                step="agent7_push",
            )
            emit_metric(
                "webhook_step_result",
                metric_name="WebhookStepLatencyMs",
                value=_elapsed_ms(step_started),
                unit="Milliseconds",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                webhook_type=webhook_type,
                step="agent7_push",
            )
        except Exception:
            logger.error(
                "ca_webhook_agent_7_push_failure application_id=%s case_identifier=%s",
                application_context.application_id,
                case_identifier,
                exc_info=True,
            )
            emit_metric(
                "agent_7_push_failure",
                provider=COMPLYADVANTAGE_PROVIDER_NAME,
                trace_id=trace_id,
                component="webhook_storage",
                outcome="failure",
                active_provider=active_provider,
                step="agent7_push",
                application_id=application_context.application_id,
                case_identifier=case_identifier,
            )
    else:
        emit_metric(
            "agent_7_push_skipped",
            metric_name="Agent7PushSkipped",
            trace_id=trace_id,
            component="webhook_storage",
            outcome="skipped",
            active_provider=active_provider,
            step="agent7_push",
        )
        emit_metric(
            "shadow_ca_activity",
            metric_name="ShadowCaActivity",
            trace_id=trace_id,
            component="webhook_storage",
            outcome="success",
            active_provider=active_provider,
        )
        emit_audit(
            "ca_agent7_push_skipped_shadow_mode",
            trace_id=trace_id,
            component="webhook_storage",
            outcome="skipped",
            application_id=application_context.application_id,
            client_id=application_context.client_id,
            webhook_type=webhook_type,
            case_identifier=case_identifier,
            customer_identifier=customer_identifier,
            decision_context="shadow_mode",
        )
    final_outcome = "success"
    emit_metric(
        "webhook_processing_latency",
        metric_name="WebhookProcessingLatencyMs",
        value=_elapsed_ms(processing_started),
        unit="Milliseconds",
        trace_id=trace_id,
        component="webhook_storage",
        outcome=final_outcome,
        webhook_type=webhook_type,
    )
    emit_operational(
        "ca_webhook_processing_completed",
        trace_id=trace_id,
        component="webhook_storage",
        outcome=final_outcome,
        webhook_type=webhook_type,
        case_identifier=case_identifier,
        customer_identifier=customer_identifier,
        application_id=application_context.application_id,
        client_id=application_context.client_id,
        normalized_record_id=normalized_record_id,
        duration_ms=_elapsed_ms(processing_started),
    )
    return {"status": "processed", "normalized_record_id": normalized_record_id}


def _lookup_subscription(db, customer_identifier):
    rows = db.execute(
        """
        SELECT client_id, application_id, person_key, customer_identifier, status
        FROM screening_monitoring_subscriptions
        WHERE provider = ? AND customer_identifier = ?
        """,
        (COMPLYADVANTAGE_PROVIDER_NAME, customer_identifier),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        return "ambiguous"
    return rows[0]


def _application_context_from_subscription(row):
    person_key = _value(row, "person_key")
    return ScreeningApplicationContext(
        application_id=_value(row, "application_id"),
        client_id=_value(row, "client_id"),
        screening_subject_kind="subject" if person_key else "entity",
        screening_subject_name=_value(row, "customer_identifier"),
        screening_subject_person_key=person_key,
    )


def _upsert_monitoring_alert(db, row):
    db.execute(
        """
        INSERT INTO monitoring_alerts
            (provider, case_identifier, application_id, client_name, alert_type, severity,
             detected_by, summary, source_reference, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            status = EXCLUDED.status
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
        ),
    )


def _default_db_factory():
    from db import get_db
    return get_db()


def _call_fetch_normalized(fetch_normalized, client, envelope, application_context, trace_id):
    if _accepts_keyword(fetch_normalized, "trace_id"):
        return fetch_normalized(client, envelope, application_context, trace_id=trace_id)
    return fetch_normalized(client, envelope, application_context)


def _default_agent_executor():
    from supervisor.agent_executors import execute_adverse_media_pep
    return execute_adverse_media_pep


def _default_db_path():
    from config import DB_PATH
    return DB_PATH


def _value(row, key):
    if isinstance(row, dict):
        return row.get(key)
    return row[key]


def _accepts_keyword(callable_obj, keyword):
    try:
        parameters = signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind == Parameter.VAR_KEYWORD or parameter.name == keyword
        for parameter in parameters
    )


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)


def _commit(db):
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()


def _rollback(db):
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        rollback()
    conn = getattr(db, "conn", None)
    if conn is not None and hasattr(conn, "rollback"):
        conn.rollback()


def _close(db):
    close = getattr(db, "close", None)
    if callable(close):
        close()
