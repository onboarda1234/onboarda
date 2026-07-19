"""Monitoring subscription persistence for ComplyAdvantage screenings."""

import logging
import asyncio
import threading

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from .observability import emit_audit, emit_metric


logger = logging.getLogger(__name__)


def seed_monitoring_subscription(
    db,
    client_id,
    application_id,
    customer_identifier,
    person_key=None,
    source="c3_create_and_screen",
    schedule_backfill=True,
    backfill_scheduler=None,
):
    """Insert one monitoring subscription row using only the injected DB handle."""
    columns = ["client_id", "application_id", "provider", "customer_identifier", "source"]
    values = [client_id, application_id, COMPLYADVANTAGE_PROVIDER_NAME, customer_identifier, source]
    if person_key:
        columns.insert(3, "person_key")
        values.insert(3, person_key)

    placeholders = ", ".join(_placeholder() for _ in columns)
    sql = (
        f"INSERT INTO screening_monitoring_subscriptions "
        f"({', '.join(columns)}) VALUES ({placeholders})"
    )
    try:
        db.execute(sql, tuple(values))
        commit = getattr(db, "commit", None)
        if callable(commit):
            commit()
        emit_metric(
            "monitoring_subscription_seeded",
            metric_name="MonitoringSubscriptionSeeded",
            component="subscriptions",
            outcome="success",
            step="subscription_seed",
        )
        emit_audit(
            "ca_subscription_seeded",
            component="subscriptions",
            outcome="success",
            application_id=application_id,
            client_id=client_id,
            customer_identifier=customer_identifier,
        )
        if schedule_backfill:
            scheduler = backfill_scheduler or _schedule_historical_backfill
            scheduler(
                application_id=application_id,
                client_id=client_id,
                customer_identifier=customer_identifier,
                person_key=person_key,
            )
    except Exception as exc:
        if _is_unique_violation(exc):
            logger.warning(
                "ca_monitoring_subscription_duplicate provider=%s client_id=%s customer_identifier=%s",
                COMPLYADVANTAGE_PROVIDER_NAME,
                client_id,
                customer_identifier,
            )
            emit_metric(
                "monitoring_subscription_duplicate",
                metric_name="MonitoringSubscriptionDuplicates",
                component="subscriptions",
                outcome="skipped",
                step="subscription_seed",
            )
            return
        raise


def find_subscription_customer_identifier(db, application_id, person_key=None):
    """Return the stored Mesh customer UUID for one screening subject, or None.

    SRP-2a Phase D: the rescreen pathway needs the MESH-ASSIGNED customer UUID
    (never our external identifier). Persons are keyed by (application_id,
    person_key); the entity subject is the application's NULL-person_key row.
    Entity rows are read oldest-first because the company is always the first
    subject screened (and therefore seeded) for an application. Any lookup
    ambiguity or failure returns None so the caller falls back to the existing
    create-and-screen path, where the conflict classification stays the
    fail-closed net.
    """
    if person_key:
        sql = (
            "SELECT customer_identifier FROM screening_monitoring_subscriptions "
            "WHERE application_id = ? AND provider = ? AND person_key = ? AND status = 'active' "
            "ORDER BY id DESC"
        )
        params = (str(application_id), COMPLYADVANTAGE_PROVIDER_NAME, str(person_key))
    else:
        sql = (
            "SELECT customer_identifier FROM screening_monitoring_subscriptions "
            "WHERE application_id = ? AND provider = ? AND person_key IS NULL AND status = 'active' "
            "ORDER BY id ASC"
        )
        params = (str(application_id), COMPLYADVANTAGE_PROVIDER_NAME)
    try:
        rows = db.execute(sql, params).fetchall()
    except Exception:
        logger.warning(
            "ca_rescreen_subscription_lookup_failed application_id=%s", application_id, exc_info=True
        )
        return None
    for row in rows:
        try:
            customer_identifier = row["customer_identifier"]
        except (TypeError, IndexError, KeyError):
            customer_identifier = row[0] if row else None
        if customer_identifier:
            return customer_identifier
    return None


def application_has_active_subscriptions(db, application_id):
    """True when the application has ANY active CA monitoring subscription."""
    try:
        row = db.execute(
            "SELECT COUNT(*) AS n FROM screening_monitoring_subscriptions "
            "WHERE application_id = ? AND provider = ? AND status = 'active'",
            (str(application_id), COMPLYADVANTAGE_PROVIDER_NAME),
        ).fetchone()
    except Exception:
        logger.warning(
            "ca_rescreen_subscription_count_failed application_id=%s", application_id, exc_info=True
        )
        return False
    if row is None:
        return False
    try:
        count = row["n"]
    except (TypeError, IndexError, KeyError):
        count = row[0] if row else 0
    return bool(count)


def update_monitoring_subscription_event(db, client_id, customer_identifier, last_webhook_type, trace_id=None):
    """Record the latest CA monitoring webhook event for an existing subscription."""
    db.execute(
        """
        UPDATE screening_monitoring_subscriptions
        SET monitoring_event_count = monitoring_event_count + 1,
            last_event_at = CURRENT_TIMESTAMP,
            last_webhook_type = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE client_id = ? AND provider = ? AND customer_identifier = ?
        """,
        (last_webhook_type, client_id, COMPLYADVANTAGE_PROVIDER_NAME, customer_identifier),
    )
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()
    emit_metric(
        "subscription_update_success",
        metric_name="SubscriptionUpdateSuccesses",
        trace_id=trace_id,
        component="subscriptions",
        outcome="success",
        webhook_type=last_webhook_type,
        step="subscription_update",
    )


def _is_unique_violation(exc):
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return (
        "unique" in text
        or "duplicate key" in text
        or "uq_screening_monitoring_subs_customer" in text
    )


def _placeholder():
    # The repository's DBConnection convention translates '?' for PostgreSQL.
    return "?"


def _schedule_historical_backfill(*, application_id, client_id, customer_identifier, person_key=None):
    """Launch the one-shot CA historical backfill after a subscription seed."""
    async def _runner():
        from db import get_db
        from .webhook_fetch import build_default_client
        from .historical_backfill import run_historical_backfill_for_subscription

        backfill_db = get_db()
        try:
            try:
                await run_historical_backfill_for_subscription(
                    db=backfill_db,
                    ca_client=build_default_client(),
                    application_id=application_id,
                    client_id=client_id,
                    customer_identifier=customer_identifier,
                    person_key=person_key,
                    discovered_via="webhook_backfill",
                    trigger_reason="subscription_seed",
                )
            except Exception:
                logger.warning(
                    "ca_historical_backfill_seed_schedule_failed client_id=%s application_id=%s",
                    client_id,
                    application_id,
                    exc_info=True,
                )
        finally:
            close = getattr(backfill_db, "close", None)
            if callable(close):
                close()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Seed can be called from synchronous onboarding code; use a one-shot
        # daemon thread only as the no-loop fallback, not as a recurring sweep.
        thread = threading.Thread(target=lambda: asyncio.run(_runner()), daemon=True)
        thread.start()
        return None
    return loop.create_task(_runner())
