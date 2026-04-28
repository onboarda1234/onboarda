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

    placeholders = ", ".join("?" for _ in columns)
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


def _is_unique_violation(exc):
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return (
        "unique" in text
        or "duplicate key" in text
        or "uq_screening_monitoring_subs_customer" in text
    )
