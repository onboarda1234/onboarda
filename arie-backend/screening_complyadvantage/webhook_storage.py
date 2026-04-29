"""Storage orchestration for ComplyAdvantage webhook dual-write processing."""

import logging
from datetime import datetime, timezone

from screening_config import get_active_provider_name
from screening_storage import persist_normalized_report

from .normalizer import ScreeningApplicationContext
from .subscriptions import update_monitoring_subscription_event
from .webhook_fetch import build_default_client, fetch_webhook_single_pass
from .webhook_mapping import map_normalized_to_monitoring_alert

logger = logging.getLogger(__name__)


def emit_metric(name, **fields):
    logger.info("ca_webhook_metric metric=%s fields=%s", name, fields)


async def process_complyadvantage_webhook(
    envelope,
    *,
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
        return {"status": "subscription_missing"}
    if subscription == "ambiguous":
        logger.error(
            "ca_webhook_subscription_ambiguous webhook_type=%s case_identifier=%s customer_identifier=%s",
            envelope.webhook_type,
            case_identifier,
            customer_identifier,
        )
        return {"status": "subscription_ambiguous"}

    # Step 2 — Pure compute: build ScreeningApplicationContext from subscription.
    application_context = _application_context_from_subscription(subscription)

    # Step 3 — Read-only: fetch-back through shared three-layer helpers.
    client = client_factory()
    normalized_report = fetch_normalized(client, envelope, application_context)
    normalized_report.setdefault("application_id", application_context.application_id)

    # Step 4 — Pure compute: normalization completed by webhook_fetch.
    source_hash = normalized_report.get("source_screening_report_hash")
    if not source_hash:
        raise ValueError("normalized CA report missing source_screening_report_hash")

    normalized_record_id = None
    db = db_factory()
    try:
        # Step 5 — REQUIRED idempotent: upsert provider-truth normalized record.
        try:
            normalized_record_id = persist_report(
                db,
                application_context.client_id,
                application_context.application_id,
                normalized_report,
                source_hash,
                provider="complyadvantage",
                normalized_version="2.0",
            )
            _commit(db)
        except Exception:
            _rollback(db)
            logger.error(
                "ca_webhook_normalized_write_failure case_identifier=%s customer_identifier=%s",
                case_identifier,
                customer_identifier,
                exc_info=True,
            )
            emit_metric("normalized_write_failure", provider="complyadvantage")
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
            _upsert_monitoring_alert(db, alert_row)
            _commit(db)
        except Exception:
            _rollback(db)
            logger.error(
                "ca_webhook_monitoring_alerts_write_failure case_identifier=%s customer_identifier=%s",
                case_identifier,
                customer_identifier,
                exc_info=True,
            )
            emit_metric("monitoring_alerts_write_failure", provider="complyadvantage")
    finally:
        _close(db)

    db = db_factory()
    try:
        # Step 8 — BEST-EFFORT (failure logs + metric, sequence continues): update subscription event metadata.
        try:
            update_monitoring_subscription_event(db, customer_identifier, envelope.webhook_type)
        except Exception:
            _rollback(db)
            logger.warning(
                "ca_webhook_subscription_update_failure case_identifier=%s customer_identifier=%s",
                case_identifier,
                customer_identifier,
                exc_info=True,
            )
    finally:
        _close(db)

    # Step 9 — BEST-EFFORT (failure logs + metric, sequence continues): flag-aware Agent 7 push.
    if get_active_provider_name() == "complyadvantage":
        try:
            executor = agent_executor or _default_agent_executor()
            context = {"db_path": _default_db_path()}
            executor(application_context.application_id, context)
        except Exception:
            logger.error(
                "ca_webhook_agent_7_push_failure application_id=%s case_identifier=%s",
                application_context.application_id,
                case_identifier,
                exc_info=True,
            )
            emit_metric("agent_7_push_failure", provider="complyadvantage")
    return {"status": "processed", "normalized_record_id": normalized_record_id}


def _lookup_subscription(db, customer_identifier):
    rows = db.execute(
        """
        SELECT client_id, application_id, person_key, customer_identifier, status
        FROM screening_monitoring_subscriptions
        WHERE provider = ? AND customer_identifier = ?
        """,
        ("complyadvantage", customer_identifier),
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
