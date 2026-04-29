"""Monitoring subscription persistence for ComplyAdvantage screenings."""

import logging


logger = logging.getLogger(__name__)


def seed_monitoring_subscription(
    db,
    client_id,
    application_id,
    customer_identifier,
    person_key=None,
    source="c3_create_and_screen",
):
    """Insert one monitoring subscription row using only the injected DB handle."""
    columns = ["client_id", "application_id", "provider", "customer_identifier", "source"]
    values = [client_id, application_id, "complyadvantage", customer_identifier, source]
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
    except Exception as exc:
        if _is_unique_violation(exc):
            logger.warning(
                "ca_monitoring_subscription_duplicate provider=%s client_id=%s customer_identifier=%s",
                "complyadvantage",
                client_id,
                customer_identifier,
            )
            return
        raise


def update_monitoring_subscription_event(db, customer_identifier, last_webhook_type):
    """Record the latest CA monitoring webhook event for an existing subscription."""
    db.execute(
        """
        UPDATE screening_monitoring_subscriptions
        SET monitoring_event_count = monitoring_event_count + 1,
            last_event_at = CURRENT_TIMESTAMP,
            last_webhook_type = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE provider = ? AND customer_identifier = ?
        """,
        (last_webhook_type, "complyadvantage", customer_identifier),
    )
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()


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
