import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from screening_complyadvantage.models.webhooks import CACaseAlertListUpdatedWebhook
from screening_complyadvantage.webhook_storage import process_complyadvantage_webhook


class NoCloseDB:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args):
        return self.conn.execute(*args)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        pass


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE screening_monitoring_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            person_key TEXT,
            customer_identifier TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            last_event_at TEXT,
            last_webhook_type TEXT,
            monitoring_event_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE monitoring_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT,
            case_identifier TEXT,
            application_id TEXT,
            client_name TEXT,
            alert_type TEXT,
            severity TEXT,
            detected_by TEXT,
            summary TEXT,
            source_reference TEXT,
            status TEXT DEFAULT 'open'
        );
        CREATE UNIQUE INDEX uq_monitoring_alerts_provider_case
            ON monitoring_alerts(provider, case_identifier)
            WHERE provider IS NOT NULL AND case_identifier IS NOT NULL;
        CREATE TABLE screening_reports_normalized (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'sumsub',
            normalized_version TEXT NOT NULL DEFAULT '1.0',
            source_screening_report_hash TEXT,
            normalized_report_json TEXT,
            normalization_status TEXT NOT NULL DEFAULT 'success',
            normalization_error TEXT,
            is_authoritative INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'migration_scaffolding',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX uq_screening_normalized_app_provider_hash
            ON screening_reports_normalized(application_id, provider, source_screening_report_hash);
        """
    )
    conn.execute(
        "INSERT INTO screening_monitoring_subscriptions (client_id, application_id, provider, person_key, customer_identifier) VALUES (?, ?, ?, ?, ?)",
        ("client-1", "app-1", "complyadvantage", "person-1", "cust-1"),
    )
    conn.commit()
    return conn


def _envelope():
    return CACaseAlertListUpdatedWebhook.model_validate({
        "webhook_type": "CASE_ALERT_LIST_UPDATED",
        "api_version": "2.0",
        "account_identifier": "acct-test",
        "case_identifier": "case-1",
        "alert_identifiers": ["alert-1"],
        "customer": {"identifier": "cust-1", "external_identifier": "app-1", "version": 1},
        "subjects": [{"identifier": "subj-1", "external_identifier": "person-1", "type": "person"}],
    })


def _normalized(hash_value="hash-1"):
    return {
        "provider": "complyadvantage",
        "source_screening_report_hash": hash_value,
        "provider_specific": {
            "complyadvantage": {
                "matches": [{"indicators": [{"type": "CAPEPIndicator", "taxonomy_key": "r_pep_class_2"}]}],
                "workflows": {"strict": {"alerts": [{"identifier": "alert-1"}]}},
            }
        },
    }


@pytest.mark.asyncio
async def test_process_sequence_writes_normalized_alert_subscription_and_agent(monkeypatch):
    conn = _db()
    agent = MagicMock()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "complyadvantage")
    monkeypatch.setattr("screening_complyadvantage.webhook_storage._default_db_path", lambda: "/tmp/test.db")

    result = await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized(),
        agent_executor=agent,
    )

    assert result["status"] == "processed"
    assert conn.execute("SELECT COUNT(*) FROM screening_reports_normalized").fetchone()[0] == 1
    alert = conn.execute("SELECT * FROM monitoring_alerts").fetchone()
    assert alert["provider"] == "complyadvantage"
    assert alert["case_identifier"] == "case-1"
    sub = conn.execute("SELECT monitoring_event_count, last_webhook_type FROM screening_monitoring_subscriptions").fetchone()
    assert sub["monitoring_event_count"] == 1
    assert sub["last_webhook_type"] == "CASE_ALERT_LIST_UPDATED"
    agent.assert_called_once_with("app-1", {"db_path": "/tmp/test.db"})


@pytest.mark.asyncio
async def test_missing_subscription_halts_before_writes():
    conn = _db()
    conn.execute("DELETE FROM screening_monitoring_subscriptions")
    called = MagicMock()

    result = await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        fetch_normalized=called,
    )

    assert result["status"] == "subscription_missing"
    called.assert_not_called()
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_normalized_write_failure_halts_best_effort_steps(monkeypatch):
    conn = _db()
    agent = MagicMock()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "complyadvantage")

    def fail_persist(*args, **kwargs):
        raise sqlite3.OperationalError("write failed")

    result = await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized(),
        persist_report=fail_persist,
        agent_executor=agent,
    )

    assert result["status"] == "normalized_write_failure"
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 0
    agent.assert_not_called()


@pytest.mark.asyncio
async def test_monitoring_alert_failure_continues_to_subscription_update_and_agent(monkeypatch):
    conn = _db()
    conn.execute("DROP TABLE monitoring_alerts")
    agent = MagicMock()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "complyadvantage")
    monkeypatch.setattr("screening_complyadvantage.webhook_storage._default_db_path", lambda: "/tmp/test.db")

    result = await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized(),
        agent_executor=agent,
    )

    assert result["status"] == "processed"
    sub = conn.execute("SELECT monitoring_event_count FROM screening_monitoring_subscriptions").fetchone()
    assert sub["monitoring_event_count"] == 1
    agent.assert_called_once()


@pytest.mark.asyncio
async def test_sumsub_provider_flag_skips_agent_push(monkeypatch):
    conn = _db()
    agent = MagicMock()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")

    await process_complyadvantage_webhook(
        _envelope(),
        db_factory=lambda: NoCloseDB(conn),
        client_factory=lambda: object(),
        fetch_normalized=lambda client, envelope, context: _normalized(),
        agent_executor=agent,
    )

    agent.assert_not_called()


@pytest.mark.asyncio
async def test_double_fire_idempotency_dedups_rows_but_counts_each_delivery(monkeypatch):
    conn = _db()
    monkeypatch.setattr("screening_complyadvantage.webhook_storage.get_active_provider_name", lambda: "sumsub")

    kwargs = {
        "db_factory": lambda: NoCloseDB(conn),
        "client_factory": lambda: object(),
        "fetch_normalized": lambda client, envelope, context: _normalized("hash-double-fire"),
    }

    first = await process_complyadvantage_webhook(_envelope(), **kwargs)
    second = await process_complyadvantage_webhook(_envelope(), **kwargs)

    assert first["status"] == "processed"
    assert second["status"] == "processed"
    assert conn.execute("SELECT COUNT(*) FROM screening_reports_normalized").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM monitoring_alerts").fetchone()[0] == 1
    sub = conn.execute(
        "SELECT monitoring_event_count, last_webhook_type "
        "FROM screening_monitoring_subscriptions WHERE customer_identifier = ?",
        ("cust-1",),
    ).fetchone()
    assert sub["monitoring_event_count"] == 2
    assert sub["last_webhook_type"] == "CASE_ALERT_LIST_UPDATED"
