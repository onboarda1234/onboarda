import logging
import sqlite3

from screening_provider import COMPLYADVANTAGE_PROVIDER_NAME
from screening_complyadvantage.subscriptions import seed_monitoring_subscription, update_monitoring_subscription_event


CREATE_TABLE = """
CREATE TABLE screening_monitoring_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL,
    application_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    person_key TEXT,
    customer_identifier TEXT NOT NULL,
    external_subscription_id TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'paused', 'cancelled', 'expired')),
    subscribed_at TEXT DEFAULT (datetime('now')),
    last_event_at TEXT,
    last_webhook_type TEXT,
    monitoring_event_count INTEGER NOT NULL DEFAULT 0,
    is_authoritative INTEGER NOT NULL DEFAULT 0
        CHECK(is_authoritative = 0),
    source TEXT NOT NULL DEFAULT 'migration_scaffolding',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX uq_screening_monitoring_subs_customer
    ON screening_monitoring_subscriptions (client_id, provider, customer_identifier);
"""


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(CREATE_TABLE)
    return conn


def test_seed_monitoring_subscription_writes_expected_columns_and_defaults():
    db = _db()

    seed_monitoring_subscription(db, "client-1", "app-1", "cust-1", person_key="person-1")

    row = db.execute("SELECT * FROM screening_monitoring_subscriptions").fetchone()
    assert row["client_id"] == "client-1"
    assert row["application_id"] == "app-1"
    assert row["provider"] == COMPLYADVANTAGE_PROVIDER_NAME
    assert row["person_key"] == "person-1"
    assert row["customer_identifier"] == "cust-1"
    assert row["source"] == "c3_create_and_screen"
    assert row["status"] == "active"
    assert row["is_authoritative"] == 0
    assert row["monitoring_event_count"] == 0
    assert row["external_subscription_id"] is None


def test_seed_monitoring_subscription_duplicate_logs_warning(caplog):
    db = _db()
    seed_monitoring_subscription(db, "client-1", "app-1", "cust-1")

    with caplog.at_level(logging.WARNING, logger="screening_complyadvantage.subscriptions"):
        seed_monitoring_subscription(db, "client-1", "app-1", "cust-1")

    assert "ca_monitoring_subscription_duplicate" in caplog.text
    assert db.execute("SELECT COUNT(*) FROM screening_monitoring_subscriptions").fetchone()[0] == 1


def test_seed_monitoring_subscription_uses_injected_handle_only(monkeypatch):
    import screening_complyadvantage.subscriptions as subscriptions

    assert "sqlite3" not in subscriptions.__dict__
    assert "psycopg2" not in subscriptions.__dict__


def test_update_monitoring_subscription_event_increments_and_sets_last_fields():
    db = _db()
    seed_monitoring_subscription(db, "client-1", "app-1", "cust-1")

    update_monitoring_subscription_event(db, "client-1", "cust-1", "CASE_ALERT_LIST_UPDATED")
    update_monitoring_subscription_event(db, "client-1", "cust-1", "CASE_CREATED")

    row = db.execute(
        "SELECT monitoring_event_count, last_webhook_type, last_event_at "
        "FROM screening_monitoring_subscriptions WHERE customer_identifier='cust-1'"
    ).fetchone()
    assert row["monitoring_event_count"] == 2
    assert row["last_webhook_type"] == "CASE_CREATED"
    assert row["last_event_at"] is not None


def test_update_monitoring_subscription_event_uses_full_client_provider_customer_key():
    db = _db()
    seed_monitoring_subscription(db, "client-1", "app-1", "shared-cust")
    seed_monitoring_subscription(db, "client-2", "app-2", "shared-cust")

    update_monitoring_subscription_event(db, "client-2", "shared-cust", "CASE_ALERT_LIST_UPDATED")

    rows = db.execute(
        "SELECT client_id, monitoring_event_count, last_webhook_type "
        "FROM screening_monitoring_subscriptions WHERE customer_identifier='shared-cust' "
        "ORDER BY client_id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["client_id"] == "client-1"
    assert rows[0]["monitoring_event_count"] == 0
    assert rows[0]["last_webhook_type"] is None
    assert rows[1]["client_id"] == "client-2"
    assert rows[1]["monitoring_event_count"] == 1
    assert rows[1]["last_webhook_type"] == "CASE_ALERT_LIST_UPDATED"
