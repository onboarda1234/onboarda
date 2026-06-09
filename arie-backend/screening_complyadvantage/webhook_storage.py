"""Storage orchestration for ComplyAdvantage webhook dual-write processing."""

import logging
import time
from datetime import datetime, timezone

from screening_config import get_active_provider_name
from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_storage import persist_normalized_report

from .evidence import evidence_hash, extract_monitoring_evidence
from .normalizer import ScreeningApplicationContext
from .observability import accepts_keyword, emit_audit, emit_metric as _emit_metric, emit_operational
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
    webhook_id=None,
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
    best_effort_failed = False
    webhook_claimed = False

    db = db_factory()
    try:
        claim = _claim_webhook_delivery(
            db,
            webhook_id=webhook_id,
            webhook_type=webhook_type,
            case_identifier=case_identifier,
            customer_identifier=customer_identifier,
            trace_id=trace_id,
        )
        webhook_claimed = claim.get("claimed", False)
        if claim.get("duplicate"):
            emit_metric(
                "webhook_duplicate_ignored",
                metric_name="WebhookDuplicateIgnored",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="duplicate",
                webhook_type=webhook_type,
                case_identifier=case_identifier,
            )
            emit_operational(
                "ca_webhook_duplicate_ignored",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="duplicate",
                webhook_type=webhook_type,
                case_identifier=case_identifier,
                customer_identifier=customer_identifier,
            )
            return {"status": "duplicate_ignored", "webhook_id": webhook_id}
    finally:
        _close(db)

    try:
        return await _process_claimed_webhook(
            envelope,
            trace_id=trace_id,
            webhook_id=webhook_id,
            db_factory=db_factory,
            client_factory=client_factory,
            fetch_normalized=fetch_normalized,
            persist_report=persist_report,
            agent_executor=agent_executor,
            processing_started=processing_started,
            webhook_claimed=webhook_claimed,
        )
    except Exception as exc:
        if webhook_claimed:
            db = db_factory()
            try:
                _finish_webhook_delivery(
                    db,
                    webhook_id,
                    status="failed",
                    result="exception",
                    failure_reason=exc.__class__.__name__,
                )
            finally:
                _close(db)
        emit_metric(
            "webhook_processing_failed",
            metric_name="WebhookProcessingFailed",
            trace_id=trace_id,
            component="webhook_storage",
            outcome="failure",
            webhook_type=webhook_type,
            case_identifier=case_identifier,
        )
        raise


async def _process_claimed_webhook(
    envelope,
    *,
    trace_id=None,
    webhook_id=None,
    db_factory,
    client_factory,
    fetch_normalized,
    persist_report,
    agent_executor=None,
    processing_started=None,
    webhook_claimed=False,
):
    customer_identifier = getattr(envelope.customer, "identifier", None)
    case_identifier = envelope.case_identifier
    webhook_type = getattr(envelope, "webhook_type", "none")
    processing_started = processing_started or time.monotonic()
    final_outcome = "failure"
    best_effort_failed = False

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
        if webhook_claimed:
            db = db_factory()
            try:
                _finish_webhook_delivery(db, webhook_id, status="failed", result="subscription_missing", failure_reason="subscription_missing")
            finally:
                _close(db)
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
        if webhook_claimed:
            db = db_factory()
            try:
                _finish_webhook_delivery(db, webhook_id, status="failed", result="subscription_ambiguous", failure_reason="subscription_ambiguous")
            finally:
                _close(db)
        return {"status": "subscription_ambiguous"}

    # Step 2 — Pure compute: build ScreeningApplicationContext from subscription.
    application_context = _application_context_from_subscription(subscription)

    # Step 3 — Read-only: fetch-back through shared three-layer helpers.
    client = client_factory()
    fetch_started = time.monotonic()
    emit_metric(
        "detail_fetch_attempted",
        metric_name="DetailFetchAttempted",
        trace_id=trace_id,
        component="webhook_storage",
        outcome="attempted",
        webhook_type=webhook_type,
        case_identifier=case_identifier,
    )
    try:
        normalized_report = _call_fetch_normalized(fetch_normalized, client, envelope, application_context, trace_id)
    except Exception:
        emit_metric(
            "detail_fetch_failed",
            metric_name="DetailFetchFailed",
            trace_id=trace_id,
            component="webhook_storage",
            outcome="failure",
            webhook_type=webhook_type,
            case_identifier=case_identifier,
        )
        raise
    emit_metric(
        "detail_fetch_succeeded",
        metric_name="DetailFetchSucceeded",
        trace_id=trace_id,
        component="webhook_storage",
        outcome="success",
        webhook_type=webhook_type,
        case_identifier=case_identifier,
    )
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
            best_effort_failed = True
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
            monitoring_alert_id, created = _upsert_monitoring_alert(db, alert_row)
            evidence_count = _persist_monitoring_alert_evidence(
                db,
                monitoring_alert_id,
                application_context.application_id,
                alert_row,
                normalized_report,
            )
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
                "alert_created" if created else "alert_updated",
                metric_name="MonitoringAlertCreated" if created else "MonitoringAlertUpdated",
                trace_id=trace_id,
                component="webhook_storage",
                outcome="success",
                case_identifier=case_identifier,
                monitoring_alert_id=monitoring_alert_id,
                evidence_count=evidence_count,
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
                monitoring_alert_id=monitoring_alert_id,
                evidence_count=evidence_count,
                webhook_type=webhook_type,
                case_identifier=case_identifier,
                customer_identifier=customer_identifier,
            )
        except Exception:
            _rollback(db)
            best_effort_failed = True
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
            best_effort_failed = True
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
    final_outcome = "failure" if best_effort_failed else "success"
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
    if webhook_claimed:
        db = db_factory()
        try:
            _finish_webhook_delivery(db, webhook_id, status="processed", result=final_outcome, failure_reason="")
        finally:
            _close(db)
    emit_metric(
        "webhook_processed",
        metric_name="WebhookProcessed",
        trace_id=trace_id,
        component="webhook_storage",
        outcome=final_outcome,
        webhook_type=webhook_type,
        case_identifier=case_identifier,
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
    before = db.execute(
        "SELECT id FROM monitoring_alerts WHERE provider = ? AND case_identifier = ?",
        (row["provider"], row["case_identifier"]),
    ).fetchone()
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
    after = db.execute(
        "SELECT id FROM monitoring_alerts WHERE provider = ? AND case_identifier = ?",
        (row["provider"], row["case_identifier"]),
    ).fetchone()
    return _row_value(after, "id"), before is None


def _persist_monitoring_alert_evidence(db, monitoring_alert_id, application_id, alert_row, normalized_report):
    evidence_rows = extract_monitoring_evidence(
        normalized_report,
        case_identifier=alert_row.get("case_identifier"),
        alert_identifier=_source_reference_value(alert_row, "alert_identifier"),
    )
    count = 0
    for entry in evidence_rows:
        row_hash = evidence_hash(entry)
        db.execute(
            """
            INSERT INTO monitoring_alert_evidence
                (monitoring_alert_id, application_id, provider, case_identifier, alert_identifier,
                 match_identifier, risk_identifier, profile_identifier, evidence_type,
                 matched_subject_name, relationship_to_client, match_category, risk_indicator,
                 match_confidence, source_title, source_name, source_url, source_url_available,
                 source_url_unavailable_reason, publication_date, snippet, provider_case_url,
                 evidence_json, raw_provider_reference, evidence_status, evidence_hash, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(monitoring_alert_id, evidence_hash)
            DO UPDATE SET
                application_id = EXCLUDED.application_id,
                matched_subject_name = EXCLUDED.matched_subject_name,
                relationship_to_client = EXCLUDED.relationship_to_client,
                match_category = EXCLUDED.match_category,
                risk_indicator = EXCLUDED.risk_indicator,
                match_confidence = EXCLUDED.match_confidence,
                source_title = EXCLUDED.source_title,
                source_name = EXCLUDED.source_name,
                source_url = EXCLUDED.source_url,
                source_url_available = EXCLUDED.source_url_available,
                source_url_unavailable_reason = EXCLUDED.source_url_unavailable_reason,
                publication_date = EXCLUDED.publication_date,
                snippet = EXCLUDED.snippet,
                provider_case_url = EXCLUDED.provider_case_url,
                evidence_json = EXCLUDED.evidence_json,
                raw_provider_reference = EXCLUDED.raw_provider_reference,
                evidence_status = EXCLUDED.evidence_status,
                fetched_at = EXCLUDED.fetched_at,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                monitoring_alert_id,
                application_id,
                entry.get("provider"),
                entry.get("case_identifier"),
                entry.get("alert_identifier"),
                entry.get("match_identifier"),
                entry.get("risk_identifier"),
                entry.get("profile_identifier"),
                entry.get("evidence_type"),
                entry.get("matched_subject_name"),
                entry.get("relationship_to_client"),
                entry.get("match_category"),
                entry.get("risk_indicator"),
                str(entry.get("match_confidence") or ""),
                entry.get("source_title"),
                entry.get("source_name"),
                entry.get("source_url"),
                bool(entry.get("source_url_available")),
                entry.get("source_url_unavailable_reason"),
                entry.get("publication_date"),
                entry.get("snippet"),
                entry.get("provider_case_url"),
                _json(entry.get("evidence_json") or {}),
                _json(entry.get("raw_provider_reference") or {}),
                entry.get("evidence_status") or "fetched",
                row_hash,
                entry.get("fetched_at"),
            ),
        )
        count += 1
    return count


def _claim_webhook_delivery(db, *, webhook_id, webhook_type, case_identifier, customer_identifier, trace_id):
    if not webhook_id:
        return {"claimed": False, "duplicate": False}
    existing = db.execute(
        "SELECT processing_status FROM complyadvantage_webhook_deliveries WHERE webhook_id = ?",
        (webhook_id,),
    ).fetchone()
    if existing:
        db.execute(
            """
            UPDATE complyadvantage_webhook_deliveries
               SET last_seen_at = CURRENT_TIMESTAMP,
                   duplicate_count = COALESCE(duplicate_count, 0) + 1,
                   webhook_type = COALESCE(NULLIF(webhook_type, ''), ?),
                   case_identifier = COALESCE(NULLIF(case_identifier, ''), ?),
                   customer_identifier = COALESCE(NULLIF(customer_identifier, ''), ?),
                   trace_id = COALESCE(NULLIF(trace_id, ''), ?)
             WHERE webhook_id = ?
            """,
            (webhook_type, case_identifier, customer_identifier, trace_id, webhook_id),
        )
        _commit(db)
        return {"claimed": False, "duplicate": True}
    db.execute(
        """
        INSERT INTO complyadvantage_webhook_deliveries
            (webhook_id, webhook_type, case_identifier, customer_identifier, processing_status,
             processing_result, trace_id, first_received_at, last_seen_at)
        VALUES (?, ?, ?, ?, 'processing', '', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (webhook_id, webhook_type, case_identifier, customer_identifier, trace_id),
    )
    _commit(db)
    return {"claimed": True, "duplicate": False}


def _finish_webhook_delivery(db, webhook_id, *, status, result, failure_reason=""):
    if not webhook_id:
        return
    db.execute(
        """
        UPDATE complyadvantage_webhook_deliveries
           SET processing_status = ?,
               processing_result = ?,
               failure_reason = ?,
               processed_at = CURRENT_TIMESTAMP,
               last_seen_at = CURRENT_TIMESTAMP
         WHERE webhook_id = ?
        """,
        (status, result, failure_reason, webhook_id),
    )
    _commit(db)


def _default_db_factory():
    from db import get_db
    return get_db()


def _call_fetch_normalized(fetch_normalized, client, envelope, application_context, trace_id):
    if accepts_keyword(fetch_normalized, "trace_id"):
        return fetch_normalized(client, envelope, application_context, trace_id=trace_id)
    return fetch_normalized(client, envelope, application_context)


def _source_reference_value(alert_row, key):
    import json
    try:
        ref = json.loads(alert_row.get("source_reference") or "{}")
    except Exception:
        ref = {}
    return ref.get(key)


def _json(value):
    import json
    return json.dumps(value, default=str, sort_keys=True)


def _row_value(row, key):
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        return row[0]


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
